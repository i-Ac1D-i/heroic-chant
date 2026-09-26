"""The in-game server list: which servers the title screen's server switch offers.

How the 1.2.389 client does it, read off the disassembly:

* The *region* list comes from ``real_serverinfo.json`` (our boot server).
  ``NGNetCenterServer.Connect`` only ever dials the selected region's
  ``centerServerIP``/``serverport`` (``NMServerInfo.GetServerInfo`` returns a
  ``DownloadServerInfo``).  Regions are keyed by ``ServiceCountryType``, which
  has four values, so they cannot carry an open-ended list.
* The *servers* under a region are ``GetServerGroupInfoAck``: a list of
  ``NGServerGroupInfo``.  Picking one stores its GroupID
  (``CenterServerGroup.SetGroupID`` + ``Write``).  From then on
  ``NMServerInfo.GetServerInfo`` @0x146F748 builds the center address from
  *that entry's* ``CenterServerIp``/``CenterServerPort``
  (``CenterServerGroup.GetGroupInfo``), and the client moves to that center if
  it isn't the one it is on -- then asks it ``GetConnectGameServerInfoReq``
  and dials the game server that comes back.  So every entry here names *this*
  server as its center: the client stays with this list (another server's
  center would show that server's list, with no way back to a player's own
  local server), and only the game-server address points elsewhere.  Found on
  the device: with the far server's address in CenterServerIp, picking it
  replaced the list with the far server's own.
* ``ServerName`` goes through ``int.Parse`` into ``NCTMPro.SetStringID``
  (``ServerGroupUIBar.SetServerGroup``): it must be a string-table id, or the
  client throws and login stalls.
* The client's account id comes only from the center -- ``vecAccountInfo``
  (matched by ``ServerGroup``) or ``CreateAccountInfoAck``, which stores it
  unchecked before asking for the game server
  (``NGNetCenterServer.CreateAccountInfoAck`` @0x1A55724).  No login packet
  carries it, and the client compares it against guild members, arena
  opponents and so on, so it has to be the real one.

So this server's center doubles as a directory.  For a server that is not this
one, the account comes from that server: ``CreateAccountInfoReq`` is forwarded
to its own center (``remote_account``, a minimal client of the same protocol)
and the answer is cached per device, so the list can show the player's name
and level there next time.  A remote server only ever hears about a device when
the player picks it.

Where the list comes from: this server itself (``directory.include_local``),
``directory.servers`` in settings, and any shared lists at
``directory.lists`` (JSON files ``{"servers": [...]}``), refreshed in the
background.  See docs/SERVERS.md.
"""
import asyncio
import json
import logging
import os
import threading
import urllib.request
from datetime import datetime

from . import config
from .data.tables import TABLES
from .game.player import ACCOUNTS_DIR
from .protocol import wire
from .protocol.dto import TYPES, encode_packet, decode_packet
from .settings import SETTINGS

log = logging.getLogger('hc.directory')

LISTS_CACHE = os.path.join(ACCOUNTS_DIR, 'directory_lists.json')
ACCOUNTS_CACHE = os.path.join(ACCOUNTS_DIR, 'directory_accounts.json')
MAX_LIST_BYTES = 256 * 1024
REMOTE_TIMEOUT = 6.0

_LISTS = {}                # url -> {'fetched': iso, 'servers': [...], 'error': str|None}
_LOCK = threading.Lock()
_WAKE = threading.Event()
_TEXT_INDEX = None


# --------------------------------------------------------------------- names

def _text_index():
    """Lower-cased string-table text -> its lowest id."""
    global _TEXT_INDEX
    if _TEXT_INDEX is None:
        idx = {}
        for sid, text in sorted(TABLES.strings().items()):
            if isinstance(text, str) and text.strip():
                idx.setdefault(text.strip().lower(), int(sid))
        _TEXT_INDEX = idx
    return _TEXT_INDEX


def name_id(value, fallback=None):
    """A string-table id for a server name.

    ``value`` may be an id (int or digits) or text that exactly matches a
    string ("Global", "Japan", a hero's name ...), case aside.  Anything else
    falls back to the string that reads as ``fallback`` (the server's id, if
    the table has that number), else to config's SERVER_NAME_STRING_ID.  The
    result is always a real id: a non-numeric ServerName stalls the client.
    """
    strings = TABLES.strings()
    if isinstance(value, bool):
        value = None
    if isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit()):
        if int(value) in strings:
            return int(value)
    elif isinstance(value, str) and value.strip():
        hit = _text_index().get(value.strip().lower())
        if hit is not None:
            return hit
    if fallback is not None:
        hit = _text_index().get(str(fallback))
        if hit is not None:
            return hit
    return int(config.SERVER_NAME_STRING_ID)


def name_text(sid):
    return TABLES.strings().get(int(sid), '')


# ------------------------------------------------------------------ entries

def _valid(raw, source):
    """A cleaned entry, or (None, why)."""
    if not isinstance(raw, dict):
        return None, 'not an object'
    gid, host, port = raw.get('id'), raw.get('host'), raw.get('port', 21010)
    if isinstance(gid, bool) or not isinstance(gid, int) or gid < 1:
        return None, 'id must be a whole number from 1 up'
    if not isinstance(host, str) or not host.strip() or any(c.isspace() for c in host.strip()):
        return None, 'host must be an address with no spaces'
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        return None, 'port must be 1-65535'
    sid = name_id(raw.get('name'), fallback=gid)
    return {'id': gid, 'host': host.strip(), 'port': port, 'name_id': sid,
            'name': name_text(sid), 'recommend': bool(raw.get('recommend', False)),
            'local': False, 'source': source}, None


def entries():
    """(servers, problems): what the list shows, in order, and what was left
    out and why.  Never empty -- with nothing else to list, this server is
    listed."""
    d = SETTINGS.get('directory') or {}
    out, problems, ids, addrs = [], [], set(), set()

    sid = name_id(d.get('local_name') or config.SERVER_NAME_STRING_ID)
    local = {'id': int(config.SERVER_GROUP_ID), 'host': config.PUBLIC_HOST,
             'port': int(config.PORT), 'name_id': sid, 'name': name_text(sid),
             'recommend': True, 'local': True, 'source': 'this server'}

    def add(entry, where):
        if entry['id'] in ids:
            problems.append('%s: id %d is already taken -- skipped' % (where, entry['id']))
            return
        if (entry['host'].lower(), entry['port']) in addrs:
            problems.append('%s: %s:%d is already listed -- skipped'
                            % (where, entry['host'], entry['port']))
            return
        ids.add(entry['id'])
        addrs.add((entry['host'].lower(), entry['port']))
        out.append(entry)

    if d.get('include_local', True):
        add(local, 'this server')
    sources = [('settings', e) for e in (d.get('servers') or [])]
    for url in d.get('lists') or []:
        sources += [(url, e) for e in (_LISTS.get(url) or {}).get('servers', [])]
    for source, raw in sources:
        entry, why = _valid(raw, source)
        where = '%s entry %s' % (source, raw.get('id') if isinstance(raw, dict) else '?')
        if entry is None:
            problems.append('%s: %s -- skipped' % (where, why))
            continue
        add(entry, where)

    if not out:
        out.append(local)
    if not any(e['recommend'] for e in out):
        out[0] = dict(out[0], recommend=True)
    return out, problems


def find(group_id):
    return next((e for e in entries()[0] if e['id'] == int(group_id)), None)


def default_entry():
    listed = entries()[0]
    return next((e for e in listed if e['recommend']), listed[0])


def group_info(entry):
    # The center is always this server -- see the module docstring.
    return TYPES['NGServerGroupInfo'](
        GroupID=entry['id'], CenterServerIp=config.PUBLIC_HOST,
        CenterServerPort=int(config.PORT), ServerName=str(entry['name_id']),
        UserCount=1, RecommendServer=1 if entry['recommend'] else 0)


# ------------------------------------------------------------ remote accounts

async def remote_account(host, port, device_id, auth_type=20, nation='',
                         timeout=REMOTE_TIMEOUT):
    """Ask another server's center for this device's account there (it makes
    one if there is none, as it would for the client).  Returns its
    NGAccountInfo.  Raises on any network or protocol failure."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)

    async def frame():
        head = await reader.readexactly(4)
        size = int.from_bytes(head, 'little')
        if size < wire.HEADSIZE or size > wire.MAX_PACKET_SIZE:
            raise ConnectionError('bogus frame size %d from %s:%d' % (size, host, port))
        return wire.parse_frame(head + await reader.readexactly(size - 4))

    async def talk():
        pid, _seq, _ctx, _body = await frame()
        if pid != wire.SC_HOSTID_INFO:
            raise ConnectionError('%s:%d did not greet like a game server' % (host, port))
        writer.write(wire.build_frame(wire.CS_HOSTID_RECV, 1, b''))
        # ServerGroupID 0 is never a listed id, so the far side treats it as
        # itself even if it runs a directory of its own.
        writer.write(wire.build_frame(10002, 2, encode_packet(
            10002, device_id, int(auth_type), nation or '', 0)))
        await writer.drain()
        while True:
            pid, _seq, _ctx, body = await frame()
            if pid == 20002:
                return decode_packet(20002, body)['_info']

    try:
        return await asyncio.wait_for(talk(), timeout)
    finally:
        writer.close()


def _load_json(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, indent=1)
    os.replace(tmp, path)


_ACCOUNT_FIELDS = ('AccountID', 'AuthType', 'BlockType', 'NickName', 'Exp',
                   'RepresentProfile', 'SkinID')
_ACCOUNT_DATES = ('BlockEndDate', 'PurchaseAgeLimitDate', 'RegDate', 'LastLogInDate')


def remember_account(device_id, group_id, info):
    row = {k: getattr(info, k) for k in _ACCOUNT_FIELDS}
    row.update({k: getattr(info, k).isoformat() for k in _ACCOUNT_DATES})
    with _LOCK:
        cache = _load_json(ACCOUNTS_CACHE)
        cache.setdefault(device_id, {})[str(int(group_id))] = row
        _save_json(ACCOUNTS_CACHE, cache)


def cached_account_infos(device_id, listed):
    """NGAccountInfo for the remote servers in ``listed`` this device has used."""
    known = _load_json(ACCOUNTS_CACHE).get(device_id, {})
    out = []
    for e in listed:
        row = known.get(str(e['id']))
        if e['local'] or not row:
            continue
        out.append(account_info_from(device_id, e['id'], row))
    return out


def account_info_from(device_id, group_id, row):
    kw = {k: row[k] for k in _ACCOUNT_FIELDS if k in row}
    for k in _ACCOUNT_DATES:
        try:
            kw[k] = datetime.fromisoformat(row[k])
        except (KeyError, TypeError, ValueError):
            pass
    return TYPES['NGAccountInfo'](DeviceID=device_id, StatDeviceID=device_id,
                                  ServerGroup=int(group_id),
                                  frameInfo=TYPES['NGFrameInfo'](), **kw)


async def account_on(entry, device_id, auth_type=20, nation=''):
    """This device's NGAccountInfo on a remote server, labelled with the
    server's id here, or None if the server cannot be reached."""
    try:
        info = await remote_account(entry['host'], entry['port'], device_id,
                                    auth_type, nation)
    except Exception as exc:                          # noqa: BLE001 -- any failure
        log.warning('server %d (%s:%d) did not answer for an account: %s',
                    entry['id'], entry['host'], entry['port'], exc)
        return None
    info.ServerGroup = entry['id']
    info.DeviceID = info.StatDeviceID = device_id
    remember_account(device_id, entry['id'], info)
    log.info('account %d on server %d (%s:%d)', info.AccountID, entry['id'],
             entry['host'], entry['port'])
    return info


# -------------------------------------------------------------- shared lists

def fetch_list(url, timeout=8.0):
    """The servers in one shared list.  http(s) only."""
    if not url.lower().startswith(('http://', 'https://')):
        raise ValueError('only http:// and https:// lists are fetched')
    req = urllib.request.Request(url, headers={'User-Agent': 'heroic-chant-server'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_LIST_BYTES + 1)
    if len(raw) > MAX_LIST_BYTES:
        raise ValueError('list is over %d KB' % (MAX_LIST_BYTES // 1024))
    data = json.loads(raw.decode('utf-8-sig'))
    servers = data.get('servers') if isinstance(data, dict) else data
    if not isinstance(servers, list):
        raise ValueError('expected {"servers": [...]}')
    return servers


def refresh_lists():
    """Fetch every configured list; a failed fetch keeps what it had."""
    urls = [u for u in (SETTINGS.get('directory.lists') or []) if isinstance(u, str)]
    for url in urls:
        old = _LISTS.get(url) or {}
        try:
            servers = fetch_list(url)
            _LISTS[url] = {'fetched': datetime.utcnow().isoformat(timespec='seconds'),
                           'servers': servers, 'error': None}
            log.info('server list %s: %d entries', url, len(servers))
        except Exception as exc:                      # noqa: BLE001
            _LISTS[url] = dict(old, error=str(exc), servers=old.get('servers', []))
            log.warning('server list %s: %s', url, exc)
    for url in list(_LISTS):
        if url not in urls:
            _LISTS.pop(url, None)
    with _LOCK:
        _save_json(LISTS_CACHE, _LISTS)


def list_status():
    out = {}
    for url, rec in _LISTS.items():
        out[url] = {k: v for k, v in rec.items() if k != 'servers'}
        out[url]['count'] = len(rec.get('servers', []))
    return out


def refresh_now():
    _WAKE.set()


def start_refresher():
    """Load the cached lists, then keep them fresh in a background thread."""
    _LISTS.update(_load_json(LISTS_CACHE))

    def loop():
        while True:
            try:
                if SETTINGS.get('directory.lists'):
                    refresh_lists()
            except Exception:                         # noqa: BLE001
                log.exception('refreshing the server lists failed')
            minutes = SETTINGS.get('directory.refresh_minutes', 15)
            try:
                minutes = max(1.0, float(minutes))
            except (TypeError, ValueError):
                minutes = 15.0
            _WAKE.wait(minutes * 60)
            _WAKE.clear()

    threading.Thread(target=loop, name='server-lists', daemon=True).start()
