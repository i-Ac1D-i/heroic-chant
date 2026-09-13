"""The boot server's overlay: modified client files, and getting phones to fetch them.

    python tools/test_bootserver_overlay.py

A phone only downloads a bundle when its manifest crc changes
(NMPatcher.CheckCompareFile @0x1F53120), so a modified bundle has to be served
with a *new, true* crc -- and everything untouched has to keep the crc 0 it has
always been served with, or every existing install re-downloads 862 MB.  This
checks both, for a client tree on disk (the PC) and inside a zip (the phone).

Builds its own fake client tree; needs no game files.
"""
import json
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s  %s%s' % ('PASS' if cond else 'FAIL', name,
                          '' if cond else ('  -- ' + str(detail))))


def build_tree(base):
    root = os.path.join(base, 'files', 'ngelgames')
    os.makedirs(os.path.join(root, 'AssetBundle', 'script'))
    with open(os.path.join(root, 'downloadInfoAndroid.json'), 'w') as fh:
        json.dump({'manifestCRC': 'x', 'patchlist': [
            {'assetbundle': True, 'path': 'script/unit', 'crc': 111, 'mb': 1,
             'label': 'LEVEL0'},
            {'assetbundle': True, 'path': 'script/string', 'crc': 222, 'mb': 1,
             'label': 'LEVEL0'},
        ]}, fh)
    for name, body in (('unit', b'original unit'), ('string', b'original string')):
        with open(os.path.join(root, 'AssetBundle', 'script', name), 'wb') as fh:
            fh.write(body)
    for marker in ('assetList.json.gz', 'dnsinfo.json'):
        open(os.path.join(root, marker), 'wb').close()
    return root


def zip_tree(base, root):
    path = os.path.join(base, 'ngelgames.zip')
    with zipfile.ZipFile(path, 'w') as z:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                full = os.path.join(dirpath, f)
                z.write(full, os.path.relpath(full, base).replace(os.sep, '/'))
    return path


def entries(obj):
    return {e['path']: e for e in obj['patchlist']}


def main():
    import bootserver
    bootserver.VERBOSE = False
    base = tempfile.mkdtemp(prefix='hc-overlay-')
    try:
        root = build_tree(base)
        archive = zip_tree(base, root)
        overlay = os.path.join(base, 'client-overrides')
        os.makedirs(os.path.join(overlay, 'AssetBundle', 'script'))
        with open(os.path.join(overlay, 'AssetBundle', 'script', 'unit'), 'wb') as fh:
            fh.write(b'MODIFIED unit, a different length')

        for label, source in (('directory', root), ('zip', archive)):
            print('\nclient files as a %s' % label)
            bootserver.ASSETS = bootserver.Assets(source, overlay=None)
            e = entries(bootserver.download_info('downloadInfoAndroid.json'))
            check('without an overlay every crc is still 0',
                  e['script/unit']['crc'] == 0 and e['script/string']['crc'] == 0)

            bootserver.ASSETS = bootserver.Assets(source, overlay=overlay)
            key = bootserver.ASSETS.find('AssetBundle', 'script', 'unit')
            check('the overlay copy is found first', bootserver.ASSETS.is_overlay(key),
                  key)
            with bootserver.ASSETS.open(key) as fh:
                check('and it is what gets served', fh.read().startswith(b'MODIFIED'))
            other = bootserver.ASSETS.find('AssetBundle', 'script', 'string')
            with bootserver.ASSETS.open(other) as fh:
                check('files not in the overlay come from the originals',
                      fh.read() == b'original string')

            e = entries(bootserver.download_info('downloadInfoAndroid.json'))
            check('an overlay file with no crc listed still serves crc 0',
                  e['script/unit']['crc'] == 0, e['script/unit'])
            check('and its size is the overlay size',
                  e['script/unit']['mb'] == len(b'MODIFIED unit, a different length'))

            with open(os.path.join(overlay, 'crc.json'), 'w') as fh:
                json.dump({'script/unit': 4200600147}, fh)
            e = entries(bootserver.download_info('downloadInfoAndroid.json'))
            check('with crc.json the overlay file gets its real crc',
                  e['script/unit']['crc'] == 4200600147, e['script/unit'])
            check('untouched files keep crc 0, so nothing else re-downloads',
                  e['script/string']['crc'] == 0, e['script/string'])

            with open(os.path.join(overlay, 'crc.json'), 'w') as fh:
                json.dump({'script/unit': 1234}, fh)
            e = entries(bootserver.download_info('downloadInfoAndroid.json'))
            check('crc.json is re-read on every request -- no restart needed',
                  e['script/unit']['crc'] == 1234)

            with open(os.path.join(overlay, 'crc.json'), 'w') as fh:
                fh.write('{not json')
            e = entries(bootserver.download_info('downloadInfoAndroid.json'))
            check('a broken crc.json falls back to 0 rather than failing',
                  e['script/unit']['crc'] == 0)
            os.remove(os.path.join(overlay, 'crc.json'))
            if bootserver.ASSETS.zip is not None:
                bootserver.ASSETS.zip.close()

            # Started before the overlay folder existed (the normal first run):
            # a tool creating it later must not need a boot server restart.
            later = os.path.join(base, 'later-overrides-' + label)
            bootserver.ASSETS = bootserver.Assets(source, overlay=later)
            e = entries(bootserver.download_info('downloadInfoAndroid.json'))
            check('a missing overlay folder is harmless', e['script/unit']['crc'] == 0)
            os.makedirs(os.path.join(later, 'AssetBundle', 'script'))
            with open(os.path.join(later, 'AssetBundle', 'script', 'unit'), 'wb') as fh:
                fh.write(b'MODIFIED later')
            with open(os.path.join(later, 'crc.json'), 'w') as fh:
                json.dump({'script/unit': 77}, fh)
            e = entries(bootserver.download_info('downloadInfoAndroid.json'))
            check('an overlay folder created after startup is picked up',
                  e['script/unit']['crc'] == 77 and e['script/unit']['mb'] == len(b'MODIFIED later'),
                  e['script/unit'])
            shutil.rmtree(later)
            e = entries(bootserver.download_info('downloadInfoAndroid.json'))
            check('and deleting it goes back to the originals at crc 0',
                  e['script/unit']['crc'] == 0 and e['script/unit']['mb'] != len(b'MODIFIED later'))
            if bootserver.ASSETS.zip is not None:
                bootserver.ASSETS.zip.close()
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        print('FAILED: ' + ', '.join(FAIL))
        return 1
    print('ALL CHECKS PASSED (%d)' % len(PASS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
