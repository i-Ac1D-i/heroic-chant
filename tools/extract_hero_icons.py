"""Pull hero portrait sprites out of the client's spritepacker/units/profilelong
bundle.

Sprite names are ``profile_01_<Skin_ID>``, where ``Skin_ID`` is exactly
``UnitList.Skin_ID`` -- so the sprite name maps straight onto a hero's UnitID
by way of that one table lookup, same idea as ``extract_icons.py``.

    python tools/extract_hero_icons.py [<game-root>] [-o hero_icons]
"""
import argparse
import os
import pathlib
import re
import sys

NAME_RE = re.compile(r'^profile_01_(\d+)$')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root', nargs='?',
                    default=os.path.join(os.path.dirname(__file__), '..', '..'),
                    help='directory holding files/ngelgames/AssetBundle')
    ap.add_argument('-o', '--out', default=os.path.join(os.path.dirname(__file__), '..', 'hero_icons'))
    args = ap.parse_args()

    try:
        import UnityPy
    except ImportError:
        sys.exit('UnityPy is required:  pip install UnityPy')

    src = pathlib.Path(args.root, 'files', 'ngelgames', 'AssetBundle',
                        'spritepacker', 'units', 'profilelong')
    if not src.exists():
        sys.exit('no spritepacker/units/profilelong bundle under %s' % args.root)

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    n = 0
    for obj in UnityPy.load(str(src)).objects:
        if obj.type.name != 'Sprite':
            continue
        data = obj.read()
        m = NAME_RE.match(data.m_Name)
        if not m:
            continue
        data.image.save(str(outdir / ('%s.png' % m.group(1))))
        n += 1

    print('%d hero portraits extracted -> %s' % (n, outdir))


if __name__ == '__main__':
    main()
