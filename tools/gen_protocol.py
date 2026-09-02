"""Regenerate hc/protocol/spec.json from the shipped client binary.

Field order on the wire is NOT declaration order, so this does not read the
Il2CppDumper text output alone.  It disassembles every

    UMessageMarshal.Write(Message, T)        (180 DTOs)
    <Ns>Proxy.<Method>(RmiContext, ...)      (859 RMI calls)

in libil2cpp.so and records the exact sequence of marshaller calls each one
makes, mapping the loaded struct offsets back to field names.  That is how we
know, for example, that NGPartyInfo.UnitID is declared but never serialised.

Inputs:
  * server-research/dump.cs        Il2CppDumper output (types, fields, RVAs)
  * server-research/protocol.json  packet id -> proxy RVA
  * lib/arm64-v8a/libil2cpp.so     extracted from the APK

Usage:
    python tools/gen_protocol.py --apk ../com.ngelgames.herocantare_1.2.389.apk
    python tools/gen_protocol.py --so /path/to/libil2cpp.so

Requires: capstone
"""
import argparse
import json
import os
import re
import struct
import sys
import tempfile
import zipfile

PRIM = {'bool', 'byte', 'short', 'ushort', 'int', 'uint', 'long', 'ulong',
        'float', 'double', 'string', 'DateTime'}
FLOATY = {'float', 'double'}
GENERIC_BASES = {'object', 'ValueType', 'Enum', 'MulticastDelegate',
                 'Exception', 'Attribute'}

CLS_RE = re.compile(r'^(?:public|internal|private|protected)?\s*'
                    r'(?:sealed\s+|abstract\s+|static\s+)*class\s+'
                    r'([A-Za-z_][\w.<>`]*)\s*(?::\s*([A-Za-z_][\w.<>`]*))?')
FIELD_RE = re.compile(r'^\s*(?:public|private|internal|protected)\s+'
                      r'(?:static\s+|readonly\s+)*([\w.<>,\[\] ]+?)\s+'
                      r'([A-Za-z_]\w*);\s*//\s*(0x[0-9A-Fa-f]+)')
CONST_RE = re.compile(r'^\s*(?:public|private|internal|protected)\s+const\s')
RVA_RE = re.compile(r'//\s*RVA:\s*(0x[0-9A-Fa-f]+)\s')
METH_RE = re.compile(r'^\s*(?:public|private|internal|protected)\s+(?:static\s+)?'
                     r'(?:override\s+|virtual\s+|abstract\s+)*[\w.<>,\[\] ]+?\s+'
                     r'([A-Za-z_]\w*)\s*\((.*?)\)\s*\{')
LDR_RE = re.compile(r'^\[(\w+)(?:,\s*#(-?0x[0-9a-f]+|-?\d+))?\]$')
REG_RE = re.compile(r'^[xw](\d+)$')
FREG_RE = re.compile(r'^[sd](\d+)$')


# ---------------------------------------------------------------- dump.cs --
def parse_dump(path):
    lines = open(path, encoding='utf-8', errors='replace').read().split('\n')
    classes, methods = {}, []
    cur, pending = None, None
    for ln in lines:
        m = CLS_RE.match(ln)
        if m:
            cur = m.group(1)
            classes.setdefault(cur, {'fields': [], 'base': m.group(2)})
            pending = None
            continue
        r = RVA_RE.search(ln)
        if r:
            pending = r.group(1)
            continue
        if not cur:
            continue
        fm = FIELD_RE.match(ln)
        if fm and not CONST_RE.match(ln):
            classes[cur]['fields'].append(
                (fm.group(1).strip(), fm.group(2), int(fm.group(3), 16)))
            continue
        mm = METH_RE.match(ln)
        if mm and pending:
            methods.append({'cls': cur, 'name': mm.group(1),
                            'params': mm.group(2), 'rva': int(pending, 16)})
            pending = None

    def merged(name, seen=None):
        seen = seen or set()
        if name in seen or name not in classes:
            return []
        seen.add(name)
        base = classes[name].get('base')
        head = merged(base, seen) if base and base not in GENERIC_BASES else []
        return head + classes[name]['fields']

    return {k: merged(k) for k in classes}, methods


# ------------------------------------------------------------------- ELF ---
class Image(object):
    def __init__(self, path):
        self.buf = open(path, 'rb').read()
        if self.buf[:4] != b'\x7fELF':
            raise ValueError('%s is not an ELF image' % path)
        phoff = struct.unpack_from('<Q', self.buf, 0x20)[0]
        phentsize = struct.unpack_from('<H', self.buf, 0x36)[0]
        phnum = struct.unpack_from('<H', self.buf, 0x38)[0]
        self.segs = []
        for i in range(phnum):
            o = phoff + i * phentsize
            p_type = struct.unpack_from('<I', self.buf, o)[0]
            p_offset, p_vaddr, _, _, p_memsz = struct.unpack_from('<QQQQQ', self.buf, o + 8)
            if p_type == 1:
                self.segs.append((p_vaddr, p_offset, p_memsz))

    def offset(self, va):
        for vaddr, off, memsz in self.segs:
            if vaddr <= va < vaddr + memsz:
                return off + (va - vaddr)
        return None


def _reg(tok):
    tok = tok.strip()
    if tok in ('xzr', 'wzr'):
        return 'zr'
    m = REG_RE.match(tok)
    if m:
        return int(m.group(1))
    m = FREG_RE.match(tok)
    return 'f' + m.group(1) if m else None


def analyze(md, img, rva, writers, maxins=1500):
    """Walk a marshalling function, returning [(kind, source)] in wire order."""
    off = img.offset(rva)
    if off is None:
        return []
    reg = {0: 'msg', 1: 'obj'}
    out = []
    for ins in md.disasm(img.buf[off:off + maxins * 4], rva):
        mn, ops = ins.mnemonic, ins.op_str
        if mn in ('ret', 'br', 'braa', 'brab', 'retab'):
            break
        if mn in ('bl', 'b'):
            tgt = int(ops.lstrip('#'), 16) if ops.startswith('#') else None
            if tgt in writers:
                kind = writers[tgt]
                src = reg.get('f0') if kind[1] in FLOATY else reg.get(1)
                out.append((kind, src))
                if mn == 'b':
                    break
            # A plain `b` elsewhere is an outlined class-init thunk that
            # returns to the next instruction, or a throw path; keep going.
            continue
        parts = [p.strip() for p in ops.split(',', 1)]
        if mn in ('mov', 'fmov') and len(parts) == 2:
            d, srcr = _reg(parts[0]), _reg(parts[1])
            if d is not None:
                reg[d] = None if srcr == 'zr' else reg.get(srcr)
        elif mn in ('ldr', 'ldrb', 'ldrh', 'ldrsw', 'ldrsh', 'ldrsb'):
            d = _reg(parts[0])
            m2 = LDR_RE.match(parts[1]) if len(parts) > 1 else None
            if d is None:
                continue
            base = reg.get(_reg(m2.group(1))) if m2 else None
            fo = (int(m2.group(2), 0) if m2.group(2) else 0) if m2 else None
            if base == 'obj':
                reg[d] = ('field', fo)
            elif isinstance(base, tuple) and base[0] == 'field':
                reg[d] = ('nested', base[1], fo)       # inlined nested DTO
            else:
                reg[d] = None
        elif mn in ('and', 'orr', 'uxtb', 'sxtw', 'ubfx', 'sbfx') and len(parts) >= 2:
            d = _reg(parts[0])
            if d is not None:
                reg[d] = reg.get(_reg(parts[1].split(',')[0]))
        elif mn in ('add', 'sub', 'csel'):
            d = _reg(parts[0])
            if d is not None:
                reg[d] = None
    return out


def classify(type_name):
    lm = re.match(r'List<(.+)>$', type_name)
    if lm:
        inner = lm.group(1).strip()
        return ('list', inner) if inner in PRIM else ('listdto', inner)
    return ('prim', type_name) if type_name in PRIM else ('dto', type_name)


def split_params(sig):
    inner = sig[sig.index('(') + 1: sig.rindex(')')]
    parts, depth, cur = [], 0, ''
    for ch in inner:
        if ch == '<':
            depth += 1
        elif ch == '>':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(cur.strip())
            cur = ''
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    out = []
    for p in parts:
        ty, nm = p.rsplit(' ', 1)
        if ty.strip() == 'RmiContext':
            continue
        out.append((classify(ty.strip()), nm))
    return out


# ------------------------------------------------------------------- main --
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument('--research', default=os.path.join(here, '..', '..', 'server-research'))
    ap.add_argument('--so', help='path to an already-extracted libil2cpp.so')
    ap.add_argument('--apk', help='APK to pull lib/arm64-v8a/libil2cpp.so from')
    ap.add_argument('-o', '--out', default=os.path.join(here, '..', 'hc', 'protocol', 'spec.json'))
    args = ap.parse_args()

    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    except ImportError:
        sys.exit('capstone is required:  pip install capstone')

    so_path, tmp = args.so, None
    if not so_path:
        if not args.apk:
            sys.exit('pass --so or --apk')
        tmp = tempfile.mkdtemp()
        so_path = os.path.join(tmp, 'libil2cpp.so')
        with zipfile.ZipFile(args.apk) as z:
            open(so_path, 'wb').write(z.read('lib/arm64-v8a/libil2cpp.so'))

    classes, methods = parse_dump(os.path.join(args.research, 'dump.cs'))
    proto = json.load(open(os.path.join(args.research, 'protocol.json')))
    img = Image(so_path)
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)

    # RVA -> what that marshaller writes
    writers = {}
    psig = re.compile(r'^Message\s+msg,\s*(.+?)\s+\w+$')
    for m in methods:
        if m['cls'] in ('UMessageMarshal', 'MessageMarshal') and m['name'] == 'Write':
            mm = psig.match(m['params'].strip())
            if mm:
                writers[m['rva']] = classify(mm.group(1).strip())
        elif m['cls'] == 'Message' and m['name'] == 'Write':
            t = m['params'].strip().split()[0]
            if t in PRIM:
                writers.setdefault(m['rva'], ('prim', t))

    # --- DTOs -------------------------------------------------------------
    dtos, problems = {}, []
    for m in methods:
        if m['cls'] != 'UMessageMarshal' or m['name'] != 'Write':
            continue
        mm = psig.match(m['params'].strip())
        if not mm:
            continue
        t = mm.group(1).strip()
        if t in PRIM or t.startswith('List<'):
            continue
        by_off = {o: (ft, fn) for ft, fn, o in classes.get(t, [])}
        fields = []
        for kind, src in analyze(md, img, m['rva'], writers):
            nested = isinstance(src, tuple) and src[0] == 'nested'
            off = src[1] if isinstance(src, tuple) else None
            decl = by_off.get(off)
            if decl is None:
                problems.append('%s: unresolved %s at %s' % (t, kind, off))
                continue
            if nested:
                if fields and fields[-1]['off'] == off:
                    continue                       # same inlined DTO, one field
                fields.append({'name': decl[1], 'kind': 'dto',
                               'type': decl[0], 'off': off})
                continue
            fields.append({'name': decl[1], 'kind': kind[0],
                           'type': kind[1], 'off': off})
        dtos[t] = [{k: f[k] for k in ('name', 'kind', 'type')} for f in fields]

    # --- packets ----------------------------------------------------------
    packets, mismatched = {}, []
    for e in proto:
        want = split_params(e['sig'])
        got = [k for k, _ in analyze(md, img, int(e['rva'], 16), writers)]
        if got != [k for k, _ in want]:
            mismatched.append('%d %s.%s' % (e['id'], e['ns'], e['name']))
        packets[str(e['id'])] = {
            'ns': e['ns'], 'name': e['name'],
            'fields': [{'name': n, 'kind': k[0], 'type': k[1]} for k, n in want]}

    json.dump({'source': 'HC 1.2.389 arm64-v8a libil2cpp.so',
               'dtos': dtos, 'packets': packets},
              open(args.out, 'w'), indent=1, sort_keys=True)

    print('%d DTOs, %d packets -> %s' % (len(dtos), len(packets), args.out))
    if problems:
        print('%d unresolved DTO fields:' % len(problems))
        for p in problems[:10]:
            print('   ', p)
    if mismatched:
        print('%d packets whose disassembly disagrees with their signature:' % len(mismatched))
        for p in mismatched[:10]:
            print('   ', p)
    else:
        print('every packet\'s disassembly matches its declared signature')
    if tmp:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
