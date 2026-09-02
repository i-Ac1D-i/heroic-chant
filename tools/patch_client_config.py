"""Rewrite the client's cached server config so it dials your machine.

The client resolves its server address from three files in the app's private
data directory (``/data/data/com.ngelgames.herocantare/files/ngelgames/``):

* ``real_patchinfo<version>.json`` -- ``serverip`` / ``serverport`` per build
* ``real_serverinfo.json``         -- center server per nation id
* ``dnsinfo.json``                 -- a baked-in hostname -> IP table that the
  client prefers over the system resolver, which is why a plain /etc/hosts or
  DNS override on its own is not always enough

This rewrites all three in place (keeping a .bak of each) so every retail
hostname points at your server.  Run it against a copy of the files directory,
then push the result back to the device.

    python tools/patch_client_config.py <files/ngelgames dir> --host 192.168.1.50

Add --restore to put the .bak files back.
"""
import argparse
import json
import os
import shutil
import sys

TARGETS = ('dnsinfo.json', 'real_serverinfo.json')


def _load(path):
    with open(path, encoding='utf-8-sig') as fh:
        return json.load(fh)


def _save(path, obj):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, indent=4)


def _backup(path):
    bak = path + '.bak'
    if not os.path.exists(bak):
        shutil.copy2(path, bak)


def patch(root, host, port):
    touched = []

    dns = os.path.join(root, 'dnsinfo.json')
    if os.path.exists(dns):
        _backup(dns)
        obj = _load(dns)
        for entry in obj.get('dnsInfo', []):
            entry['ip'] = host
        _save(dns, obj)
        touched.append('dnsinfo.json (%d hostnames -> %s)' % (len(obj.get('dnsInfo', [])), host))

    si = os.path.join(root, 'real_serverinfo.json')
    if os.path.exists(si):
        _backup(si)
        obj = _load(si)
        for entry in obj.get('centerInfo', []):
            entry['centerServerIP'] = host
            entry['serverport'] = port
        _save(si, obj)
        touched.append('real_serverinfo.json (%d nations -> %s:%d)'
                       % (len(obj.get('centerInfo', [])), host, port))

    n = 0
    for name in sorted(os.listdir(root)):
        if not (name.startswith('real_patchinfo') and name.endswith('.json')):
            continue
        path = os.path.join(root, name)
        _backup(path)
        obj = _load(path)
        for entry in obj.get('rvInfo', []):
            entry['serverip'] = host
            entry['serverport'] = port
        _save(path, obj)
        n += 1
    if n:
        touched.append('%d real_patchinfo*.json -> %s:%d' % (n, host, port))

    return touched


def restore(root):
    n = 0
    for name in sorted(os.listdir(root)):
        if name.endswith('.json.bak'):
            shutil.copy2(os.path.join(root, name), os.path.join(root, name[:-4]))
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root', help='the files/ngelgames directory')
    ap.add_argument('--host', help='IP the device should connect to')
    ap.add_argument('--port', type=int, default=21010)
    ap.add_argument('--restore', action='store_true', help='undo a previous patch')
    args = ap.parse_args()

    if not os.path.isdir(args.root):
        sys.exit('not a directory: %s' % args.root)

    if args.restore:
        print('restored %d files' % restore(args.root))
        return
    if not args.host:
        sys.exit('--host is required (use the LAN IP of the machine running the server)')

    for line in patch(args.root, args.host, args.port):
        print('  patched', line)
    print('\nPush this directory back to /data/data/com.ngelgames.herocantare/files/ngelgames/')
    print('and start the server with:  python -m hc.main --public-host %s' % args.host)


if __name__ == '__main__':
    main()
