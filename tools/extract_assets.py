"""Pull the static game tables out of the client's Unity AssetBundles.

The bundles under files/ngelgames/AssetBundle/script/ and /tutorial hold plain
JSON TextAssets: dungeon definitions, monster stats, reward tables, shop and
mission data.  (script/unit is encrypted with an "adong:" AES envelope, but its
decrypted contents are exactly the 172 tables already present in
table/herocantare.db, so we read those from SQLite instead.)

    python tools/extract_assets.py [<game-root>] [-o server/data]
"""
import argparse
import json
import os
import pathlib
import sys

BUNDLES = ['script/dungeon', 'script/resource', 'script/tutorial', 'script/test']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root', nargs='?',
                    default=os.path.join(os.path.dirname(__file__), '..', '..'),
                    help='directory holding files/ngelgames/AssetBundle')
    ap.add_argument('-o', '--out', default=os.path.join(os.path.dirname(__file__), '..', 'data'))
    args = ap.parse_args()

    try:
        import UnityPy
    except ImportError:
        sys.exit('UnityPy is required:  pip install UnityPy')

    ab = pathlib.Path(args.root, 'files', 'ngelgames', 'AssetBundle')
    if not ab.is_dir():
        sys.exit('no AssetBundle directory under %s' % args.root)

    total = 0
    for rel in BUNDLES:
        src = ab / rel
        if not src.exists():
            print('  skip (absent): %s' % rel)
            continue
        outdir = pathlib.Path(args.out, rel.split('/')[-1])
        outdir.mkdir(parents=True, exist_ok=True)
        n = 0
        for obj in UnityPy.load(str(src)).objects:
            if obj.type.name != 'TextAsset':
                continue
            d = obj.read()
            raw = d.m_Script
            if isinstance(raw, str):
                raw = raw.encode('utf-8', 'surrogateescape')
            if raw.lstrip()[:1] not in (b'[', b'{'):
                continue          # encrypted or non-JSON payload
            (outdir / (d.m_Name + '.json')).write_bytes(raw)
            n += 1
        print('  %-20s %3d tables -> %s' % (rel, n, outdir))
        total += n
    print('%d JSON tables extracted' % total)


if __name__ == '__main__':
    main()
