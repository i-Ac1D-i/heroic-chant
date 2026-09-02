"""Boot-time DNS + HTTP shim, so the client can finish starting up.

Before it ever opens a TCP socket the client fetches two JSON files over plain
HTTP from `dlhc.ngelgames.net` (base URLs are baked into global-metadata.dat):

    http://dlhc.ngelgames.net/herocantare/patchinfo/real_patchinfo<ver>.json
    http://dlhc.ngelgames.net/herocantare/serverinfo/real_serverinfo.json

That host is dead, so this serves both -- rewritten to point at your machine.
Because the patchinfo *itself* carries `serverip`/`serverport`, serving it is
usually enough on its own: the client will dial your game server without any
edit to the files on the device.

The DNS half answers A queries for the ngelgames / ngel-herocantare domains
with your IP and forwards everything else upstream, which lets you redirect a
stock, unrooted device just by setting its Wi-Fi DNS to this machine.

    # needs privileges for ports 53 and 80
    python tools/bootserver.py --host 192.168.1.50

    # HTTP only (if you are redirecting DNS some other way)
    python tools/bootserver.py --host 192.168.1.50 --no-dns

Run the game server alongside it:

    python -m hc.main --public-host 192.168.1.50
"""
import argparse
import json
import os
import socket
import socketserver
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_FILES = os.path.join(HERE, '..', '..', 'files', 'ngelgames')
BUNDLES = os.path.join(CLIENT_FILES, 'AssetBundle')

# Hostnames that should resolve to us.
MATCH = ('ngelgames.net', 'ngelgames.co.kr', 'ngelgames.com',
         'ngel-herocantare.com', 'herocantare.jp')

HOST = '127.0.0.1'
PORT = 21010
VERBOSE = True


def log(fmt, *a):
    if VERBOSE:
        print(fmt % a, flush=True)


# ------------------------------------------------------------------ HTTP --
def _load(name, fallback):
    path = os.path.join(CLIENT_FILES, name)
    if os.path.exists(path):
        with open(path, encoding='utf-8-sig') as fh:
            return json.load(fh)
    return fallback


def patchinfo(version):
    obj = _load('real_patchinfo%s.json' % version, None)
    if obj is None:
        obj = {'rvInfo': [{'version': '1.2.389', 'devicetype': d, 'service': 0,
                           'server': 2, 'message': '1075', 'labeltype': 1,
                           'resourceVersion': '22',
                           'cdninfo': 'http://%s:8080/assets/' % HOST,
                           'market': 'market://details?id=com.ngelgames.herocantare'}
                          for d in ('AOS', 'IOS')]}
    for entry in obj.get('rvInfo', []):
        entry['serverip'] = HOST
        entry['serverport'] = PORT
    return obj


def serverinfo():
    obj = _load('real_serverinfo.json', None)
    if obj is None:
        obj = {'centerInfo': [{'nationID': n, 'nationName': 34645,
                               'noticePage': '', 'guidePage': '',
                               'termPage': '', 'privacyPage': '',
                               'supportMail': 'none@localhost'}
                              for n in (10, 22, 23, 41)]}
    for entry in obj.get('centerInfo', []):
        entry['centerServerIP'] = HOST
        entry['serverport'] = PORT
    return obj


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _send_file(self, path, local, ctype):
        size = os.path.getsize(local)
        log('  HTTP %s -> %s (%d bytes)', path, os.path.basename(local), size)
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(size))
        self.end_headers()
        with open(local, 'rb') as fh:
            while True:
                chunk = fh.read(256 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _send(self, obj, code=200):
        body = json.dumps(obj, indent=4).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?')[0]
        name = path.rsplit('/', 1)[-1]

        if name.startswith('real_patchinfo') and name.endswith('.json'):
            version = name[len('real_patchinfo'):-len('.json')]
            log('  HTTP %s -> patchinfo (server %s:%d)', path, HOST, PORT)
            return self._send(patchinfo(version))

        if name == 'real_serverinfo.json':
            log('  HTTP %s -> serverinfo (center %s:%d)', path, HOST, PORT)
            return self._send(serverinfo())

        # AssetBundle downloads.  The CDN path is
        #   /herocantare/assetbundle/aos/update_aos_<ver>_<n>/Android/<sub/path>
        # and the client keeps those under files/ngelgames/AssetBundle/<sub/path>
        # with the leading platform segment dropped -- including nested ones
        # like Android/sound/voice/ch3011.  Try with and without that segment.
        if '/assetbundle/' in path.lower():
            segs = [s for s in path.split('/') if s]
            rest = None
            for i, s in enumerate(segs):
                if s.startswith('update_aos_') or s.startswith('update_ios_'):
                    rest = segs[i + 1:]
                    break
            if rest is None and 'assetbundle' in segs:
                rest = segs[segs.index('assetbundle') + 1:]
            if rest:
                variants = [rest]
                if rest[0] in ('Android', 'iOS', 'IOS') and len(rest) > 1:
                    variants.append(rest[1:])
                # Bundles live under AssetBundle/, but movies sit beside it in
                # ngelgames/movie/, so search both roots.
                for root in (BUNDLES, CLIENT_FILES):
                    for v in variants:
                        cand = os.path.join(root, *v)
                        if os.path.isfile(cand):
                            return self._send_file(path, cand,
                                                   'application/octet-stream')

        # Anything else the client asks for that we have a local copy of.
        # This covers the asset manifests it fetches from the CDN path --
        # assetList.json.gz in particular, which it will retry forever.
        local = os.path.join(CLIENT_FILES, name)
        if name and os.path.isfile(local):
            ctype = ('application/gzip' if name.endswith('.gz')
                     else 'application/json' if name.endswith('.json')
                     else 'application/octet-stream')
            return self._send_file(path, local, ctype)

        log('  HTTP %s -> 404', path)
        self.send_response(404)
        self.send_header('Content-Length', '0')
        self.end_headers()


class ThreadedHTTP(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind a port that is already
    # in use and silently steal some of the connections, which makes a stale
    # instance look like a code bug.  Only enable it where it means what it says.
    allow_reuse_address = (os.name != 'nt')


# ------------------------------------------------------------------- DNS --
def dns_name(buf, off):
    parts = []
    while True:
        n = buf[off]
        if n == 0:
            return '.'.join(parts), off + 1
        if n & 0xC0:                       # compression pointer
            return '.'.join(parts), off + 2
        parts.append(buf[off + 1:off + 1 + n].decode('ascii', 'replace'))
        off += 1 + n


def dns_serve(bind, upstream):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind, 53))
    log('DNS   listening on %s:53 (upstream %s)', bind, upstream)
    while True:
        try:
            data, addr = sock.recvfrom(2048)
            if len(data) < 13:
                continue
            qname, end = dns_name(data, 12)
            qtype = struct.unpack_from('>H', data, end)[0]
            hit = qtype in (1, 255) and any(qname.lower().endswith(m) for m in MATCH)
            if hit:
                tid = data[:2]
                resp = (tid + b'\x81\x80' + data[4:6] + b'\x00\x01'
                        + b'\x00\x00\x00\x00' + data[12:end + 4]
                        + b'\xc0\x0c' + struct.pack('>HHIH', 1, 1, 60, 4)
                        + socket.inet_aton(HOST))
                sock.sendto(resp, addr)
                log('  DNS  %s -> %s', qname, HOST)
            else:
                up = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                up.settimeout(3)
                try:
                    up.sendto(data, (upstream, 53))
                    sock.sendto(up.recvfrom(2048)[0], addr)
                except socket.timeout:
                    pass
                finally:
                    up.close()
        except Exception as exc:                       # noqa: BLE001
            log('  DNS  error: %s', exc)


# ------------------------------------------------------------------ main --
def main():
    global HOST, PORT, VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True,
                    help="this machine's LAN IP, as the device should reach it")
    ap.add_argument('--port', type=int, default=21010, help='game server port')
    ap.add_argument('--http-port', type=int, default=80)
    ap.add_argument('--bind', default='0.0.0.0')
    ap.add_argument('--upstream-dns', default='8.8.8.8')
    ap.add_argument('--no-dns', action='store_true', help='serve HTTP only')
    ap.add_argument('-q', '--quiet', action='store_true')
    args = ap.parse_args()

    HOST, PORT, VERBOSE = args.host, args.port, not args.quiet

    if not args.no_dns:
        t = threading.Thread(target=dns_serve, args=(args.bind, args.upstream_dns),
                             daemon=True)
        t.start()

    try:
        httpd = ThreadedHTTP((args.bind, args.http_port), Handler)
    except PermissionError:
        sys.exit('cannot bind port %d -- run as Administrator/root, or pass '
                 '--http-port with a port you can bind' % args.http_port)
    except OSError as exc:
        sys.exit('cannot bind port %d: %s' % (args.http_port, exc))

    log('HTTP  listening on %s:%d', args.bind, args.http_port)
    log('      clients will be sent to %s:%d', HOST, PORT)
    log('\nNow start the game server:  python -m hc.main --public-host %s\n', HOST)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log('shutting down')


if __name__ == '__main__':
    main()
