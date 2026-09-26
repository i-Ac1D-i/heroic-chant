"""Center server: account lookup and game-server referral (packets 10000-10002).

Doubles as the server directory: the list it sends can hold other servers
too, and picking one of those is answered with that server's address and the
player's account there.  See hc/directory.py.
"""
import json
import logging
import os
from datetime import datetime

from .. import config, directory
from ..net import handler
from ..game.errors import Err
from ..protocol.dto import TYPES
from ..game.player import Player, ACCOUNTS_DIR

log = logging.getLogger('hc.center')
INDEX = os.path.join(ACCOUNTS_DIR, 'index.json')


def _index():
    if os.path.exists(INDEX):
        with open(INDEX, encoding='utf-8') as fh:
            return json.load(fh)
    return {}


def account_for_device(device_id, create=True):
    """Map a device id to a stable account id, minting one on first sight."""
    idx = _index()
    if device_id in idx:
        return idx[device_id]
    if not create:
        return None
    account_id = max(idx.values(), default=1000) + 1
    idx[device_id] = account_id
    os.makedirs(ACCOUNTS_DIR, exist_ok=True)
    with open(INDEX, 'w', encoding='utf-8') as fh:
        json.dump(idx, fh, indent=1)
    log.info('new account %d for device %s', account_id, device_id)
    return account_id


def link_device(device_id, account_id):
    idx = _index()
    idx[device_id] = int(account_id)
    os.makedirs(ACCOUNTS_DIR, exist_ok=True)
    with open(INDEX, 'w', encoding='utf-8') as fh:
        json.dump(idx, fh, indent=1)


def login_account(device_id, claimed):
    """Which account a LogInReq gets.  The device decides, not the AccountID
    the client sends: with more than one server in the list, the id a client
    carries may belong to another server -- where it would be someone else's
    account here.

    A device this server has not linked yet may still claim its own save (one
    made before the device index existed): that is honoured only when the
    save was made by the same device.
    """
    known = account_for_device(device_id, create=False)
    if known is not None:
        if claimed and int(claimed) != int(known):
            log.warning('device %s asked for account %s but is linked to %d -- using %d',
                        device_id, claimed, known, known)
        return known
    if claimed:
        p = Player.load(int(claimed))
        if p is not None and p.d.get('device_id') == device_id:
            link_device(device_id, int(claimed))
            return int(claimed)
        if p is not None:
            log.warning('device %s asked for account %s, which belongs to another '
                        'device -- giving it its own', device_id, claimed)
    return account_for_device(device_id)


def account_info(player, device_id):
    now = datetime.utcnow()
    return TYPES['NGAccountInfo'](
        DeviceID=device_id,
        AccountID=player.account_id,
        AuthType=20,
        BlockType=0,
        ServerGroup=config.SERVER_GROUP_ID,
        BlockEndDate=now,
        PurchaseAgeLimitDate=now,
        RegDate=datetime.fromisoformat(player.d['reg_date']),
        LastLogInDate=now,
        NickName=player.nickname,
        Exp=player.d['exp'],
        RepresentProfile=player.d['represent_profile'],
        SkinID=player.d['skin_id'],
        frameInfo=TYPES['NGFrameInfo'](),
        StatDeviceID=device_id,
    )


@handler(10000)
async def get_server_group_info(s, a):
    """The server list, and the player's account on each server they have
    used.  Other servers are not contacted here: they only hear about a
    device once the player picks them (CreateAccountInfoReq below)."""
    device_id = a['DeviceID']
    listed, _problems = directory.entries()
    accounts = []
    if any(e['local'] for e in listed):
        account_id = account_for_device(device_id)
        player = Player.load(account_id) or Player.create(account_id, device_id)
        s.account, s.player = account_id, player
        accounts.append(account_info(player, device_id))
    accounts += directory.cached_account_infos(device_id, listed)
    await s.send(20000, Err.OK, [directory.group_info(e) for e in listed], accounts)


@handler(10001)
async def get_connect_game_server_info(s, a):
    """Where the picked server's game server is.  An id that is no longer
    listed (the client remembers the last pick) gets the recommended one."""
    entry = directory.find(a['ServerGroupID']) or directory.default_entry()
    await s.send(20001, Err.OK, entry['host'], entry['port'])


@handler(10002)
async def create_account_info(s, a):
    """The player picked a server they have no account on.  For this server
    that means making one; for another, asking it (the client stores
    whatever AccountID comes back, unchecked, and uses it from then on)."""
    device_id = a['DeviceID']
    entry = directory.find(a['ServerGroupID'])
    if entry is not None and not entry['local']:
        info = await directory.account_on(entry, device_id, a['AuthType'], a['Nation'])
        if info is None:
            # CreateAccountInfoAck ignores Error and asks for the game server
            # regardless; with AccountID 0 the far side still finds the
            # account by device at login.
            info = TYPES['NGAccountInfo'](DeviceID=device_id, StatDeviceID=device_id,
                                          ServerGroup=entry['id'],
                                          frameInfo=TYPES['NGFrameInfo']())
        await s.send(20002, Err.OK, info)
        return
    account_id = account_for_device(device_id)
    player = Player.load(account_id) or Player.create(account_id, device_id)
    s.account, s.player = account_id, player
    await s.send(20002, Err.OK, account_info(player, device_id))
