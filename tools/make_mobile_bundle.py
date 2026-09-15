"""Package the generated server data for a phone.

The phone cannot build this itself.  `gen_protocol.py` needs capstone and a
33 MB dump.cs, `extract_assets.py` needs UnityPy, and neither installs cleanly
under Termux on arm64 -- both want a compiler and a lot of patience.  So the
generation stays a desktop (or CI) job, run once, and the phone just unpacks
the result.

Produces `heroic-chant-data.zip`:

    spec.json                 packet + DTO layouts   (tools/gen_protocol.py)
    data/**.json              252 static tables      (tools/extract_assets.py)
    herocantare.db            172 SQLite tables
    icons/**.png              dashboard resource icons (tools/extract_icons.py)
    hero_icons/**.png         dashboard hero portraits (tools/extract_hero_icons.py)

which `tools/termux-setup.sh` unpacks into place.  Roughly 90 MB, versus the
~2 GB of AssetBundles that go in the separate client-files zip.  The two icon
folders are optional -- left out of the zip if you have not run their extract
scripts, and `termux-setup.sh` copes with either version of the bundle.

    python tools/make_mobile_bundle.py
    python tools/make_mobile_bundle.py -o /path/to/heroic-chant-data.zip

This is game-derived content: it is gitignored, and it is not something to
commit or attach to the repo.
"""
import argparse
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
SPEC = os.path.join(ROOT, 'hc', 'protocol', 'spec.json')
DATA = os.path.join(ROOT, 'data')
DB = os.path.join(ROOT, '..', 'table', 'herocantare.db')
ICONS = os.path.join(ROOT, 'icons')
HERO_ICONS = os.path.join(ROOT, 'hero_icons')


def add_dir(z, src, arc_prefix, ext='.png'):
    n = 0
    for dirpath, _, names in os.walk(src):
        for name in sorted(names):
            if not name.endswith(ext):
                continue
            full = os.path.join(dirpath, name)
            z.write(full, arc_prefix + '/' + os.path.relpath(full, src).replace('\\', '/'))
            n += 1
    return n


def check(path, what, fix):
    if not os.path.exists(path):
        sys.exit('missing %s (%s)\n  run: %s' % (what, path, fix))
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('-o', '--out',
                    default=os.path.join(ROOT, 'heroic-chant-data.zip'))
    ap.add_argument('--db', default=DB, help='path to herocantare.db')
    ap.add_argument('--no-db', action='store_true',
                    help='leave herocantare.db out (when it is hosted separately)')
    args = ap.parse_args()

    check(SPEC, 'spec.json', 'python tools/gen_protocol.py --apk <apk>')
    check(DATA, 'data/', 'python tools/extract_assets.py')
    db = None if args.no_db else check(
        os.path.abspath(args.db), 'herocantare.db', 'put it in table/, or pass --db')

    out = os.path.abspath(args.out)
    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(SPEC, 'spec.json')
        n += 1
        for dirpath, _, names in os.walk(DATA):
            for name in sorted(names):
                if not name.endswith('.json'):
                    continue
                full = os.path.join(dirpath, name)
                z.write(full, 'data/' + os.path.relpath(full, DATA).replace('\\', '/'))
                n += 1
        if db is not None:
            z.write(db, 'herocantare.db')
            n += 1
        if os.path.isdir(ICONS):
            k = add_dir(z, ICONS, 'icons')
            print('  + %d resource icons' % k)
            n += k
        if os.path.isdir(HERO_ICONS):
            k = add_dir(z, HERO_ICONS, 'hero_icons')
            print('  + %d hero portraits' % k)
            n += k

    print('wrote %s' % out)
    print('  %d files, %.0f MB' % (n, os.path.getsize(out) / 1e6))
    print('\nPut this on the phone along with a zip of the client\'s')
    print('files/ngelgames directory, then run tools/termux-setup.sh.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
