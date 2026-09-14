"""Force NMUserInfo.IsOpenCommandCenter() to return true.

The retail client hardcodes this to always return false, no matter the
story progress or account level (see tools/disasm.py
NMUserInfo.IsOpenCommandCenter). This patches that one instruction directly
in libil2cpp.so.

    python tools/patch_command_center.py ../com.ngelgames.herocantare_1.2.389.apk

Doesn't touch global-metadata.dat, so the usual DNS/hosts setup still works.
A re-signed APK needs a clean install:

    adb uninstall com.ngelgames.herocantare
    adb install out-commandcenter.apk

Requires Android SDK build-tools (zipalign, apksigner), a JDK (keytool), and
dump.cs.
"""
import argparse
import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from gen_protocol import parse_dump, Image           # noqa: E402
from patch_apk import find_tool, run, ensure_keystore, SIG_SUFFIXES  # noqa: E402

SO_PATH = 'lib/arm64-v8a/libil2cpp.so'
TARGET_METHOD = ('NMUserInfo', 'IsOpenCommandCenter')
ORIGINAL = bytes.fromhex('e0031f2a')
PATCHED = bytes.fromhex('20008052')
RET_OFFSET = 0x50

DEFAULT_DUMP = os.path.join(HERE, '..', '..', 'server-research', 'dump.cs')


def find_rva(dump_path):
    _, methods = parse_dump(dump_path)
    cls, name = TARGET_METHOD
    hits = [m for m in methods if m['cls'] == cls and m['name'] == name]
    if not hits:
        sys.exit('%s.%s not found in %s' % (cls, name, dump_path))
    return hits[0]['rva']


def patch_so(so_bytes, rva):
    with tempfile.NamedTemporaryFile(delete=False, suffix='.so') as f:
        f.write(so_bytes)
        tmp_path = f.name
    try:
        img = Image(tmp_path)
        off = img.offset(rva + RET_OFFSET)
    finally:
        os.remove(tmp_path)
    if off is None:
        sys.exit('RVA 0x%X does not map to any loadable segment' % rva)
    found = so_bytes[off:off + len(ORIGINAL)]
    if found != ORIGINAL:
        sys.exit('expected %s at file offset 0x%X, found %s -- this APK is '
                 'probably not 1.2.389, or the compiler laid the function out '
                 'differently. Re-derive RET_OFFSET with tools/disasm.py.'
                 % (ORIGINAL.hex(), off, found.hex()))
    patched = bytearray(so_bytes)
    patched[off:off + len(PATCHED)] = PATCHED
    return bytes(patched), off


def repack(src, dst, so_bytes):
    kept = dropped = 0
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, 'w') as zout:
        for info in zin.infolist():
            if info.filename.upper().endswith(SIG_SUFFIXES) or \
                    info.filename.upper() == 'META-INF/MANIFEST.MF':
                dropped += 1
                continue
            data = so_bytes if info.filename == SO_PATH else zin.read(info.filename)
            out = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            out.compress_type = info.compress_type
            out.external_attr = info.external_attr
            out.internal_attr = info.internal_attr
            out.create_system = info.create_system
            zout.writestr(out, data)
            kept += 1
    return kept, dropped


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('apk', help='the original 1.2.389 APK')
    ap.add_argument('-o', '--out', help='output APK (default: <name>-commandcenter.apk)')
    ap.add_argument('--dump', default=DEFAULT_DUMP)
    ap.add_argument('--keystore', default=None)
    ap.add_argument('--ks-pass', default='heroicchant')
    ap.add_argument('--ks-alias', default='heroicchant')
    args = ap.parse_args()

    src = os.path.abspath(args.apk)
    if not os.path.isfile(src):
        sys.exit('no such APK: %s' % src)
    if not os.path.exists(args.dump):
        sys.exit('no dump.cs at %s -- pass --dump' % args.dump)

    out = os.path.abspath(args.out or os.path.splitext(src)[0] + '-commandcenter.apk')
    keystore = os.path.abspath(args.keystore or
                               os.path.join(HERE, 'heroicchant.keystore'))
    staging = out + '.unaligned'

    rva = find_rva(args.dump)
    print('found     %s.%s @ RVA 0x%X' % (TARGET_METHOD[0], TARGET_METHOD[1], rva))

    with zipfile.ZipFile(src) as z:
        so_bytes = z.read(SO_PATH)
    patched, off = patch_so(so_bytes, rva)
    print('patching  libil2cpp.so @ file offset 0x%X: %s -> %s'
          % (off, ORIGINAL.hex(), PATCHED.hex()))

    kept, dropped = repack(src, staging, patched)
    print('repacked  %d entries (dropped %d signature files)' % (kept, dropped))

    zipalign = find_tool('zipalign')
    if not zipalign:
        sys.exit('zipalign not found -- install Android SDK build-tools')
    if os.path.exists(out):
        os.remove(out)
    run([zipalign, '-p', '-f', '4', staging, out], 'zipalign')
    os.remove(staging)
    print('aligned   %s' % os.path.basename(out))

    apksigner = find_tool('apksigner')
    if not apksigner:
        sys.exit('apksigner not found -- install Android SDK build-tools')
    if ensure_keystore(keystore, args.ks_alias, args.ks_pass):
        print('created   %s' % os.path.basename(keystore))
    run([apksigner, 'sign', '--ks', keystore,
         '--ks-key-alias', args.ks_alias,
         '--ks-pass', 'pass:' + args.ks_pass,
         '--key-pass', 'pass:' + args.ks_pass,
         out], 'apksigner sign')
    print('signed    %s' % os.path.basename(out))

    print('\ndone: %s' % out)
    print('\nDifferent signature than the original -- uninstall first:')
    print('    adb uninstall com.ngelgames.herocantare')
    print('    adb install "%s"' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
