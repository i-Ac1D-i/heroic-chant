"""Disassemble one client method, with the call targets named.

    python tools/disasm.py DimensionGachaTabHeroUI.GachaSummon
    python tools/disasm.py --rva 0x1A3C284
    python tools/disasm.py 'DimensionGachaTabHeroUI.*' --list

`dump.cs` gives signatures and RVAs but no bodies -- 103,639 empty methods --
so any question of the form "what does the client actually do when..." ends up
here.  `gen_protocol.py` already disassembles the marshallers; this is the same
machinery pointed at arbitrary code, with two things bolted on that turn raw
ARM64 into something readable:

  * every `bl` is resolved to `Class.Method` through dump.cs's RVA table, so
    the control flow reads as a call trace
  * `mov wN, #<imm>` is annotated with the matching client string when the
    immediate looks like a string-table id, which is how you find the code
    behind a message you saw on screen

Needs capstone and the same inputs as gen_protocol.py (dump.cs plus
libil2cpp.so, pulled straight from the APK if you point --apk at it).
"""
import argparse
import os
import re
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gen_protocol import parse_dump, Image          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESEARCH = os.path.join(ROOT, '..', 'server-research')
DEFAULT_DUMP = os.path.join(RESEARCH, 'dump.cs')
DEFAULT_APK = os.path.join(ROOT, '..', 'com.ngelgames.herocantare_1.2.389.apk')


def load_strings():
    """Client string table, for annotating string ids.  Optional."""
    try:
        from hc.data.tables import TABLES
        return TABLES.strings()
    except Exception:
        return {}


def method_index(methods):
    """rva -> 'Class.Method(params)', for naming bl targets."""
    out = {}
    for m in methods:
        out.setdefault(m['rva'], '%s.%s(%s)' % (m['cls'], m['name'], m['params']))
    return out


def find(methods, pattern):
    rx = re.compile(pattern.replace('.', r'\.').replace('*', '.*') + '$', re.I)
    hits = [m for m in methods
            if rx.match('%s.%s' % (m['cls'], m['name']))]
    if not hits:      # fall back to a loose substring match
        low = pattern.lower()
        hits = [m for m in methods
                if low in ('%s.%s' % (m['cls'], m['name'])).lower()]
    return hits


def disasm(md, img, rva, names, strings, count, show_bytes=False):
    off = img.offset(rva)
    if off is None:
        print('rva 0x%X is not inside a loaded segment' % rva)
        return
    code = img.buf[off:off + count * 4]
    for ins in md.disasm(code, rva):
        note = ''
        if ins.mnemonic == 'bl':
            try:
                target = int(ins.op_str.lstrip('#'), 16)
            except ValueError:
                target = None
            if target is not None:
                note = '   ; -> %s' % names.get(target, '0x%X (unnamed)' % target)
        elif ins.mnemonic == 'mov' and '#' in ins.op_str:
            m = re.search(r'#(0x[0-9a-f]+|\d+)$', ins.op_str)
            if m:
                val = int(m.group(1), 0)
                s = strings.get(val) or strings.get(str(val))
                if isinstance(s, str) and 3 < len(s) < 120:
                    note = '   ; string %d = %r' % (val, s)
        raw = ' %-12s' % ins.bytes.hex() if show_bytes else ''
        print('  0x%08X%s  %-8s %-40s%s' % (ins.address, raw, ins.mnemonic,
                                            ins.op_str, note))
        if ins.mnemonic == 'ret':
            break


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('target', nargs='?', help='Class.Method, or a pattern with *')
    ap.add_argument('--rva', help='disassemble this RVA instead')
    ap.add_argument('--list', action='store_true',
                    help='just list the methods that match')
    ap.add_argument('-n', '--count', type=int, default=200,
                    help='max instructions (default 200)')
    ap.add_argument('--bytes', action='store_true', help='show raw bytes')
    ap.add_argument('--dump', default=DEFAULT_DUMP)
    ap.add_argument('--apk', default=DEFAULT_APK)
    ap.add_argument('--so', help='an already-extracted libil2cpp.so')
    args = ap.parse_args(argv)

    # The client's string table is multilingual, so an annotation can carry
    # Korean or accented text.  A Windows console is cp1252 by default and
    # raises UnicodeEncodeError on the first one, which kills the whole dump
    # part-way through -- and the interesting code is usually further down.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

    if not args.target and not args.rva:
        ap.error('give a Class.Method or --rva')

    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    except ImportError:
        sys.exit('capstone is required:  pip install capstone')

    if not os.path.exists(args.dump):
        sys.exit('no dump.cs at %s -- pass --dump' % args.dump)
    _, methods = parse_dump(args.dump)
    names = method_index(methods)

    if args.target and args.list:
        for m in sorted(find(methods, args.target), key=lambda m: m['rva']):
            print('0x%08X  %s.%s(%s)' % (m['rva'], m['cls'], m['name'], m['params']))
        return 0

    tmp = None
    so_path = args.so
    if not so_path:
        if not os.path.exists(args.apk):
            sys.exit('no APK at %s -- pass --apk or --so' % args.apk)
        tmp = tempfile.mkdtemp()
        so_path = os.path.join(tmp, 'libil2cpp.so')
        with zipfile.ZipFile(args.apk) as z:
            open(so_path, 'wb').write(z.read('lib/arm64-v8a/libil2cpp.so'))

    img = Image(so_path)
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    strings = load_strings()

    if args.rva:
        targets = [{'cls': '', 'name': '(rva)', 'params': '',
                    'rva': int(args.rva, 0)}]
    else:
        targets = find(methods, args.target)
        if not targets:
            sys.exit('nothing matches %r -- try --list with a *' % args.target)
        if len(targets) > 6:
            sys.exit('%d methods match; narrow it down or use --list'
                     % len(targets))

    for m in targets:
        print('\n=== %s.%s(%s)   RVA 0x%X' % (m['cls'], m['name'],
                                              m['params'], m['rva']))
        disasm(md, img, m['rva'], names, strings, args.count, args.bytes)
    return 0


if __name__ == '__main__':
    sys.exit(main())
