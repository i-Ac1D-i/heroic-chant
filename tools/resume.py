"""Bring the whole setup back up after a reboot.

Reconnects adb, re-adds the port forwards (these are lost on every reboot and
are the usual reason nothing connects), starts the boot shim and the game
server, and optionally launches the game.

    python tools/resume.py                 # loopback route via adb reverse
    python tools/resume.py --no-launch     # start servers, launch the game yourself
    python tools/resume.py --host 192.168.1.6 --http-port 80 --no-reverse
                                           # LAN route instead (needs Administrator)

Logs go to server/logs/.  Ctrl+C stops both servers.
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
LOGS = os.path.join(ROOT, 'logs')
PKG = 'com.ngelgames.herocantare'

ADB_CANDIDATES = [
    r'C:\platform-tools\adb.exe',
    'adb',
]


def find_adb():
    for cand in ADB_CANDIDATES:
        try:
            subprocess.run([cand, 'version'], capture_output=True, timeout=20, check=True)
            return cand
        except Exception:                              # noqa: BLE001
            continue
    return None


def adb(adb_bin, serial, *args, check=False, timeout=120):
    cmd = [adb_bin] + (['-s', serial] if serial else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, check=check)


def pick_device(adb_bin, wanted):
    """Connect to the emulator and return its serial."""
    for port in (16384, 7555, 5555):
        adb(adb_bin, None, 'connect', '127.0.0.1:%d' % port, timeout=30)
    out = adb(adb_bin, None, 'devices', timeout=30).stdout
    serials = [ln.split()[0] for ln in out.splitlines()[1:]
               if ln.strip() and ln.split()[-1] == 'device']
    if not serials:
        return None
    if wanted and wanted in serials:
        return wanted
    # Prefer 16384 (MuMu 12); the other ports are the same device.
    for pref in ('127.0.0.1:16384', '127.0.0.1:7555', '127.0.0.1:5555'):
        if pref in serials:
            return pref
    return serials[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1',
                    help='address the client is told to dial (default: loopback '
                         'via adb reverse)')
    ap.add_argument('--port', type=int, default=21010)
    ap.add_argument('--http-port', type=int, default=8080,
                    help='port the boot shim binds locally')
    ap.add_argument('--serial', help='adb serial, if you have more than one device')
    ap.add_argument('--no-reverse', action='store_true',
                    help='skip adb reverse (use when going over the LAN)')
    ap.add_argument('--no-launch', action='store_true')
    args = ap.parse_args()

    adb_bin = find_adb()
    if not adb_bin:
        sys.exit('adb not found -- add platform-tools to PATH')

    serial = pick_device(adb_bin, args.serial)
    if not serial:
        sys.exit('no device: start MuMu Player and enable ADB in its settings')
    print('device: %s' % serial)

    r = adb(adb_bin, serial, 'root', timeout=60)
    print('root  : %s' % (r.stdout.strip() or r.stderr.strip() or 'already root'))

    if not args.no_reverse:
        adb(adb_bin, serial, 'reverse', '--remove-all', timeout=30)
        adb(adb_bin, serial, 'reverse', 'tcp:80', 'tcp:%d' % args.http_port, timeout=30)
        adb(adb_bin, serial, 'reverse', 'tcp:%d' % args.port,
            'tcp:%d' % args.port, timeout=30)
        print('reverse: %s' % adb(adb_bin, serial, 'reverse', '--list',
                                  timeout=30).stdout.strip().replace('\n', ' | '))

    os.makedirs(LOGS, exist_ok=True)
    boot_log = open(os.path.join(LOGS, 'boot.log'), 'w')
    game_log = open(os.path.join(LOGS, 'game.log'), 'w')

    boot = subprocess.Popen(
        [sys.executable, os.path.join(HERE, 'bootserver.py'),
         '--host', args.host, '--port', str(args.port),
         '--http-port', str(args.http_port), '--no-dns'],
        cwd=ROOT, stdout=boot_log, stderr=subprocess.STDOUT)
    game = subprocess.Popen(
        [sys.executable, '-m', 'hc.main',
         '--public-host', args.host, '--port', str(args.port)],
        cwd=ROOT, stdout=game_log, stderr=subprocess.STDOUT)
    time.sleep(3)

    for name, proc in (('bootserver', boot), ('game server', game)):
        if proc.poll() is not None:
            print('%s exited immediately -- see server/logs/' % name)
            return 1
    print('bootserver : http://%s:%d  (log: logs/boot.log)' % (args.host, args.http_port))
    print('game server: %s:%d          (log: logs/game.log)' % (args.host, args.port))

    if not args.no_launch:
        adb(adb_bin, serial, 'shell', 'am', 'force-stop', PKG, timeout=60)
        time.sleep(1)
        adb(adb_bin, serial, 'shell', 'monkey', '-p', PKG,
            '-c', 'android.intent.category.LAUNCHER', '1', timeout=60)
        print('launched %s' % PKG)

    print('\nCtrl+C to stop both servers.')
    try:
        while True:
            time.sleep(1)
            if game.poll() is not None:
                print('game server exited -- see logs/game.log')
                break
    except KeyboardInterrupt:
        print('\nstopping')
    finally:
        for proc in (boot, game):
            if proc.poll() is None:
                proc.terminate()
    return 0


if __name__ == '__main__':
    sys.exit(main())
