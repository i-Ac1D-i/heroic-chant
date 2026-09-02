"""Center server: account lookup and game-server referral (packets 10000-10002)."""
import json
import logging
import os
from datetime import datetime

from .. import config
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
    device_id = a['DeviceID']
    account_id = account_for_device(device_id)
    player = Player.load(account_id) or Player.create(account_id, device_id)
    s.account, s.player = account_id, player

    group = TYPES['NGServerGroupInfo'](
        GroupID=config.SERVER_GROUP_ID,
        CenterServerIp=config.PUBLIC_HOST,
        CenterServerPort=config.PORT,
        ServerName=str(config.SERVER_NAME_STRING_ID),
        UserCount=1,
        RecommendServer=1,
    )
    await s.send(20000, Err.OK, [group], [account_info(player, device_id)])


@handler(10001)
async def get_connect_game_server_info(s, a):
    await s.send(20001, Err.OK, config.PUBLIC_HOST, config.PORT)


@handler(10002)
async def create_account_info(s, a):
    device_id = a['DeviceID']
    account_id = account_for_device(device_id)
    player = Player.load(account_id) or Player.create(account_id, device_id)
    s.account, s.player = account_id, player
    await s.send(20002, Err.OK, account_info(player, device_id))
