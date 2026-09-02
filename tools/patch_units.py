"""Edit the client's encrypted unit tables -- e.g. make NPCs and bosses playable.

Hero data (names, art, stats, skills) lives in the `script/unit` AssetBundle,
not on the server, so anything that changes what a hero *is* has to be written
back into that bundle.  Its TextAssets are wrapped in an "adong:" envelope:

    adong: + base64( AES-256-CBC( PKCS7(json), key, iv ) )

with the key sitting in the binary as a plain constant
(`NMUnit.aeskey` = "ssdafjoasdj12312442dakuasdfhiuds") and an all-zero IV.
Re-encrypting a table byte-for-byte reproduces the shipped file, so the client
accepts what we write back.

    python tools/patch_units.py --unlock-npcs      # IsPlayerHero = 1 for units with art
    python tools/patch_units.py --restore          # put the original bundle back
    python tools/patch_units.py --list             # show what would change

The original is kept beside the bundle as `unit.orig`.
"""
import argparse
import base64
import json
import os
import shutil
import sys

KEY = b'ssdafjoasdj12312442dakuasdfhiuds'
IV = b'\0' * 16
HDR = b'adong:'

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(HERE, '..', '..', 'files', 'ngelgames',
                      'AssetBundle', 'script', 'unit')
ORIG = BUNDLE + '.orig'


def _cipher():
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher(algorithms.AES(KEY), modes.CBC(IV))


def decrypt(raw):
    from cryptography.hazmat.primitives import padding as padmod
    if isinstance(raw, str):
        raw = raw.encode('utf-8', 'surrogateescape')
    d = _cipher().decryptor()
    out = d.update(base64.b64decode(raw[len(HDR):])) + d.finalize()
    u = padmod.PKCS7(128).unpadder()
    return (u.update(out) + u.finalize()).decode('utf-8')


def encrypt(text):
    from cryptography.hazmat.primitives import padding as padmod
    p = padmod.PKCS7(128).padder()
    data = p.update(text.encode('utf-8')) + p.finalize()
    e = _cipher().encryptor()
    return HDR + base64.b64encode(e.update(data) + e.finalize())


def has_art(row):
    return str(row.get('Skin_ID', '-1')).strip() not in ('-1', '0', '')


def load_table(env, name):
    for obj in env.objects:
        if obj.type.name != 'TextAsset':
            continue
        d = obj.read()
        if d.m_Name == name:
            return obj, d
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--unlock-npcs', action='store_true',
                    help='set IsPlayerHero = 1 on every unit that has art')
    ap.add_argument('--list', action='store_true', help='show what would change')
    ap.add_argument('--restore', action='store_true', help='restore unit.orig')
    ap.add_argument('--bundle', default=BUNDLE)
    args = ap.parse_args()

    orig = args.bundle + '.orig'

    if args.restore:
        if not os.path.exists(orig):
            sys.exit('no backup at %s' % orig)
        shutil.copy2(orig, args.bundle)
        print('restored %s from %s' % (args.bundle, orig))
        return

    try:
        import UnityPy
    except ImportError:
        sys.exit('UnityPy is required:  pip install UnityPy')

    if not os.path.exists(orig):
        shutil.copy2(args.bundle, orig)
        print('backed up original -> %s' % orig)

    # Always patch from the pristine copy so runs are idempotent.
    env = UnityPy.load(orig)
    obj, data = load_table(env, 'UnitList')
    if obj is None:
        sys.exit('UnitList not found in %s' % orig)

    rows = json.loads(decrypt(data.m_Script))
    targets = [r for r in rows if r.get('IsPlayerHero') != '1' and has_art(r)]
    print('%d units total, %d already playable, %d unlockable (have art)'
          % (len(rows), sum(1 for r in rows if r.get('IsPlayerHero') == '1'),
             len(targets)))

    if args.list or not args.unlock_npcs:
        for r in targets[:15]:
            print('   id=%-8s skin=%-8s boss=%s isHero=%s'
                  % (r['UnitID'], r.get('Skin_ID'), r.get('Boss'), r.get('IsHero')))
        if not args.unlock_npcs:
            print('\n(pass --unlock-npcs to write the change)')
            return

    for r in targets:
        r['IsPlayerHero'] = '1'

    data.m_Script = encrypt(json.dumps(rows, separators=(',', ':'))).decode('ascii')
    data.save()
    with open(args.bundle, 'wb') as fh:
        fh.write(env.file.save())
    print('wrote %s (%d bytes)' % (args.bundle, os.path.getsize(args.bundle)))

    # Read it straight back so a broken repack fails here, not on the device.
    check = UnityPy.load(args.bundle)
    _, again = load_table(check, 'UnitList')
    back = json.loads(decrypt(again.m_Script))
    playable = sum(1 for r in back if r.get('IsPlayerHero') == '1')
    print('verified: %d rows, %d playable' % (len(back), playable))


if __name__ == '__main__':
    main()
