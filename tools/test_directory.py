"""The in-game server list, against a second real server.

    python tools/test_directory.py

Starts another copy of this server in a subprocess (its own accounts and
settings, no dashboard) to stand in for somebody else's server, lists it in
this one's directory, and drives the center packets the way the client does:
the list, picking the other server, the account it hands out, its address,
and a login on its game port with that account.  Also the name lookup, the
shared lists (served from a local HTTP server) and the login's device check.
See hc/directory.py.
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s  %s%s' % ('PASS' if cond else 'FAIL', name,
                          '' if cond else ('  -- ' + str(detail))))


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_listening(port, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


async def game_call(port, packet_id, args, want):
    """One request to a game port, as a client would send it; returns the
    decoded `want` Ack."""
    from hc.protocol import wire
    from hc.protocol.dto import encode_packet, decode_packet
    reader, writer = await asyncio.open_connection('127.0.0.1', port)

    async def frame():
        head = await reader.readexactly(4)
        size = int.from_bytes(head, 'little')
        return wire.parse_frame(head + await reader.readexactly(size - 4))

    try:
        pid = (await frame())[0]
        assert pid == wire.SC_HOSTID_INFO, pid
        writer.write(wire.build_frame(wire.CS_HOSTID_RECV, 1, b''))
        writer.write(wire.build_frame(packet_id, 2, encode_packet(packet_id, *args)))
        await writer.drain()
        while True:
            pid, _seq, _ctx, body = await asyncio.wait_for(frame(), 20)
            if pid == want:
                return decode_packet(want, body)
    finally:
        writer.close()


class ListHandler(BaseHTTPRequestHandler):
    body = b'{}'

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


def main():
    sandbox = tempfile.mkdtemp(prefix='hc-directory-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    os.environ['HC_ACCOUNTS_DIR'] = os.path.join(sandbox, 'accounts')
    os.makedirs(os.environ['HC_ACCOUNTS_DIR'])

    remote_dir = os.path.join(sandbox, 'remote')
    os.makedirs(os.path.join(remote_dir, 'accounts'))
    remote_port = free_port()
    env = dict(os.environ, HC_SETTINGS=os.path.join(remote_dir, 'settings.json'),
               HC_ACCOUNTS_DIR=os.path.join(remote_dir, 'accounts'))
    remote = subprocess.Popen(
        [sys.executable, '-m', 'hc.main', '--host', '127.0.0.1', '--port', str(remote_port),
         '--public-host', '127.0.0.1', '--no-web', '--log-level', 'WARNING'],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    lists_http = None

    from hc import config, directory
    from hc.settings import SETTINGS
    from hc.game.player import Player
    import hc.handlers  # noqa: F401
    from hc.handlers.center import login_account, account_for_device
    from tools.test_progression import FakeSession, call

    class CenterSession(FakeSession):
        """The center step comes before any player is known."""
        def __init__(self):
            self.player, self.account, self.sent = None, None, []

    try:
        # ------------------------------------------------------------------
        print('\nnames: the client can only show string-table text')
        check('"Global" is string 34645', directory.name_id('Global') == 34645)
        check('matching ignores case', directory.name_id('japan') == 35424)
        check('an id passes straight through', directory.name_id(34645) == 34645
              and directory.name_id('34645') == 34645)
        three = directory.name_id('no such string anywhere, surely', fallback=3)
        check('unknown text falls back to the server\'s number',
              directory.name_text(three) == '3', (three, directory.name_text(three)))
        check('and to "Global" when even that is missing',
              directory.name_id('nope nope nope', fallback=987654) == config.SERVER_NAME_STRING_ID)
        check('a bool is not an id', directory.name_id(True) == config.SERVER_NAME_STRING_ID)

        # ------------------------------------------------------------------
        print('\nthe list')
        listed, problems = directory.entries()
        check('by default it is just this server, recommended',
              [(e['id'], e['local'], e['recommend']) for e in listed] == [(1, True, True)]
              and not problems, (listed, problems))
        SETTINGS.set('directory.servers', [
            {'id': 7, 'name': 'Japan', 'host': '127.0.0.1', 'port': remote_port},
            {'id': 8, 'name': 'Catherine', 'host': '127.0.0.1', 'port': free_port()},
            {'id': 1, 'name': 'clash', 'host': 'a.example', 'port': 1},
            {'id': 9, 'host': '', 'port': 21010},
            {'id': 10, 'host': 'b.example', 'port': 70000},
            {'id': 11, 'host': '127.0.0.1', 'port': remote_port},
            {'id': 'x', 'host': 'c.example'},
        ])
        listed, problems = directory.entries()
        check('good entries are listed in order', [e['id'] for e in listed] == [1, 7, 8],
              [e['id'] for e in listed])
        check('and named from the string table',
              [e['name'] for e in listed] == ['Global', 'Japan', 'Catherine'],
              [e['name'] for e in listed])
        check('the five bad ones are left out, each with a reason', len(problems) == 5,
              problems)
        check('a taken id, a missing host, a bad port, a repeated address, a bad id',
              any('already taken' in p for p in problems)
              and any('host' in p for p in problems)
              and any('port' in p for p in problems)
              and any('already listed' in p for p in problems)
              and any('whole number' in p for p in problems), problems)

        dev = 'test-device-directory'
        s = CenterSession()
        d = call(s, 10000, DeviceID=dev, AuthType=20, DeviceType=20, UserLanguage=10)
        groups = d['vecServerGroupInfo']
        check('GetServerGroupInfoAck lists all three', [g.GroupID for g in groups] == [1, 7, 8])
        check('every ServerName is a number the client can int.Parse',
              all(g.ServerName.isdigit() for g in groups), [g.ServerName for g in groups])
        check('only this server is recommended',
              [g.RecommendServer for g in groups] == [1, 0, 0])
        check('and every entry names this server as its center, so the client '
              'stays with this list (it moves to an entry\'s CenterServerIp)',
              {(g.CenterServerIp, g.CenterServerPort) for g in groups}
              == {(config.PUBLIC_HOST, config.PORT)},
              [(g.CenterServerIp, g.CenterServerPort) for g in groups])
        check('the account list only has this server so far',
              [a.ServerGroup for a in d['vecAccountInfo']] == [1], d['vecAccountInfo'])

        # ------------------------------------------------------------------
        print('\npicking the other server')
        check('the other server is up', wait_listening(remote_port), remote_port)
        d = call(s, 10002, DeviceID=dev, AuthType=20, Nation='IT', ServerGroupID=7)
        info = d['_info']
        remote_index = os.path.join(remote_dir, 'accounts', 'index.json')
        remote_ids = json.load(open(remote_index, encoding='utf-8')) \
            if os.path.exists(remote_index) else {}
        check('CreateAccountInfoAck carries the account made over there',
              info.AccountID > 0 and remote_ids.get(dev) == info.AccountID,
              (info.AccountID, remote_ids))
        check('labelled with the id it has in this list', info.ServerGroup == 7)
        d = call(s, 10001, ServerGroupID=7)
        check('GetConnectGameServerInfo(7) sends the client to it',
              (d['HostName'], d['Port']) == ('127.0.0.1', remote_port), d)
        d = call(s, 10001, ServerGroupID=1)
        check('and (1) to this one', (d['HostName'], d['Port']) == (config.PUBLIC_HOST, config.PORT))
        d = call(s, 10001, ServerGroupID=99)
        check('an id that is not listed gets the recommended server',
              (d['HostName'], d['Port']) == (config.PUBLIC_HOST, config.PORT))

        d = call(s, 10000, DeviceID=dev, AuthType=20, DeviceType=20, UserLanguage=10)
        cached = [a for a in d['vecAccountInfo'] if a.ServerGroup == 7]
        check('next time the list shows that account too, from the cache',
              len(cached) == 1 and cached[0].AccountID == info.AccountID, d['vecAccountInfo'])

        ack = asyncio.run(game_call(remote_port, 30000, (
            dev, info.AccountID, 20, 20, '1.2.389', dev, 20, 10, 0, 22, False), 40000))
        check('logging in over there with that account works',
              ack['ngAck'].Error == 0 and ack['ngAck'].Nickname, ack['ngAck'])

        print('\na server that does not answer')
        t0 = time.time()
        d = call(s, 10002, DeviceID=dev, AuthType=20, Nation='IT', ServerGroupID=8)
        check('the pick still gets an answer, in time',
              d['_info'].ServerGroup == 8 and d['_info'].AccountID == 0
              and time.time() - t0 < directory.REMOTE_TIMEOUT + 2, (d, time.time() - t0))

        # ------------------------------------------------------------------
        print('\nshared lists')
        ListHandler.body = json.dumps({'servers': [
            {'id': 20, 'name': 'Sarah', 'host': 'shared.example', 'port': 21010},
            {'id': 7, 'name': 'dup', 'host': 'dup.example', 'port': 21010},
        ]}).encode()
        lists_http = ThreadingHTTPServer(('127.0.0.1', 0), ListHandler)
        threading.Thread(target=lists_http.serve_forever, daemon=True).start()
        url = 'http://127.0.0.1:%d/servers.json' % lists_http.server_address[1]
        SETTINGS.set('directory.lists', [url, 'file:///etc/passwd'])
        directory.refresh_lists()
        listed, problems = directory.entries()
        check('a shared list is merged in after your own',
              [e['id'] for e in listed] == [1, 7, 8, 20], [e['id'] for e in listed])
        check('its clash with your id 7 is skipped',
              any('already taken' in p and url in p for p in problems), problems)
        status = directory.list_status()
        check('a file:// list is refused, not read',
              'http' in (status.get('file:///etc/passwd') or {}).get('error', ''), status)
        lists_http.shutdown()
        lists_http = None
        directory.refresh_lists()
        check('when a list stops answering, its servers stay listed',
              any(e['id'] == 20 for e in directory.entries()[0])
              and directory.list_status()[url].get('error'), directory.list_status())
        SETTINGS.set('directory.lists', [])
        directory.refresh_lists()
        check('and a list taken out of settings is gone',
              [e['id'] for e in directory.entries()[0]] == [1, 7, 8])

        SETTINGS.set('directory.include_local', False)
        listed = directory.entries()[0]
        check('without this server, the first other one is recommended',
              [(e['id'], e['recommend']) for e in listed][:1] == [(7, True)], listed)
        SETTINGS.set('directory.servers', [])
        listed = directory.entries()[0]
        check('and with nothing at all, this server is listed anyway',
              [e['id'] for e in listed] == [1] and listed[0]['local'], listed)

        # ------------------------------------------------------------------
        print('\nwho a login is')
        a_id = account_for_device('device-a')
        b_id = account_for_device('device-b')
        Player.create(a_id, 'device-a')
        Player.create(b_id, 'device-b')
        check('a device gets its own account', login_account('device-a', a_id) == a_id)
        check('claiming another device\'s account gets your own',
              login_account('device-b', a_id) == b_id)
        check('an id from another server is ignored the same way',
              login_account('device-b', 424242) == b_id)
        Player.create(5555, 'device-old')
        check('an unlinked device may claim a save it made',
              login_account('device-old', 5555) == 5555
              and account_for_device('device-old', create=False) == 5555)
        Player.create(6666, 'device-owner')
        got = login_account('device-thief', 6666)
        check('but not one another device made', got not in (6666, None), got)
    finally:
        if lists_http is not None:
            lists_http.shutdown()
        remote.terminate()
        try:
            remote.wait(10)
        except subprocess.TimeoutExpired:
            remote.kill()
        os.environ.pop('HC_SETTINGS', None)
        os.environ.pop('HC_ACCOUNTS_DIR', None)
        shutil.rmtree(sandbox, ignore_errors=True)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        print('FAILED: ' + ', '.join(FAIL))
        return 1
    print('ALL CHECKS PASSED (%d)' % len(PASS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
