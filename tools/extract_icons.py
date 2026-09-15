"""Pull resource icon sprites out of the client's spritepacker/resource bundle.

Sprite names inside that bundle are ``resource_<s|m>_<t1>[_<t2>]`` -- s and m
are the two sizes shipped, and the numbers are exactly a resource wallet key
(``t2`` omitted means -1), so the sprite name maps straight onto
``type1:type2:type3`` with no separate lookup table needed.

    python tools/extract_icons.py [<game-root>] [-o icons]
"""
import argparse
import os
import pathlib
import re
import sys

NAME_RE = re.compile(r'^resource_([a-z]+)_(-?\d+)(?:_(-?\d+))?$')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root', nargs='?',
                    default=os.path.join(os.path.dirname(__file__), '..', '..'),
                    help='directory holding files/ngelgames/AssetBundle')
    ap.add_argument('-o', '--out', default=os.path.join(os.path.dirname(__file__), '..', 'icons'))
    args = ap.parse_args()

    try:
        import UnityPy
    except ImportError:
        sys.exit('UnityPy is required:  pip install UnityPy')

    src = pathlib.Path(args.root, 'files', 'ngelgames', 'AssetBundle', 'spritepacker', 'resource')
    if not src.exists():
        sys.exit('no spritepacker/resource bundle under %s' % args.root)

    counts = {}
    for obj in UnityPy.load(str(src)).objects:
        if obj.type.name != 'Sprite':
            continue
        data = obj.read()
        m = NAME_RE.match(data.m_Name)
        if not m:
            continue
        size, t1, t2 = m.group(1), m.group(2), m.group(3) or '-1'
        outdir = pathlib.Path(args.out, size)
        outdir.mkdir(parents=True, exist_ok=True)
        data.image.save(str(outdir / ('%s_%s.png' % (t1, t2))))
        counts[size] = counts.get(size, 0) + 1

    for size, n in sorted(counts.items()):
        print('  %-4s %5d icons -> %s' % (size, n, pathlib.Path(args.out, size)))
    print('%d icons extracted' % sum(counts.values()))


if __name__ == '__main__':
    main()
