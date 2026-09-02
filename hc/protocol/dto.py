"""Table-driven codecs for the 180 NG* DTOs and 859 RMI packets.

``spec.json`` was produced by disassembling every
``UMessageMarshal.Write(Message, T)`` and every RMI proxy in libil2cpp.so, so
the field order here is the order the shipped client actually puts on the
wire -- not the declaration order.  They differ: e.g. ``NGPartyInfo.UnitID``
is declared but never serialised.  Regenerate with ``tools/gen_protocol.py``.
"""
import json
import os
from datetime import datetime

from .wire import Writer, Reader, EPOCH

_SPEC = json.load(open(os.path.join(os.path.dirname(__file__), 'spec.json'), encoding='utf-8'))
DTO_SPEC = _SPEC['dtos']
PACKET_SPEC = {int(k): v for k, v in _SPEC['packets'].items()}

PRIM_DEFAULT = {
    'bool': False, 'byte': 0, 'short': 0, 'ushort': 0, 'int': 0, 'uint': 0,
    'long': 0, 'ulong': 0, 'float': 0.0, 'double': 0.0, 'string': '',
    'DateTime': EPOCH,
}
_W = {'bool': 'b', 'byte': 'u8', 'short': 'i16', 'ushort': 'u16', 'int': 'i32',
      'uint': 'u32', 'long': 'i64', 'ulong': 'u64', 'float': 'f32',
      'double': 'f64', 'string': 's', 'DateTime': 'dt'}


class DTO(object):
    """Base for every generated NG* type.  Fields default to zero values."""
    __slots__ = ()
    _fields = ()

    def __init__(self, **kw):
        for f in self._fields:
            setattr(self, f['name'], _default(f))
        for k, v in kw.items():
            if k not in self.__slots__:
                raise AttributeError('%s has no field %r' % (type(self).__name__, k))
            setattr(self, k, v)

    def __repr__(self):
        inner = ', '.join('%s=%r' % (f['name'], getattr(self, f['name']))
                          for f in self._fields[:6])
        more = ', ...' if len(self._fields) > 6 else ''
        return '%s(%s%s)' % (type(self).__name__, inner, more)


def _default(f):
    if f['kind'] == 'prim':
        return PRIM_DEFAULT[f['type']]
    if f['kind'] in ('list', 'listdto'):
        return []
    return None          # nested DTO: built on demand when written


TYPES = {}


def _build_types():
    for name, fields in DTO_SPEC.items():
        slots = tuple(f['name'] for f in fields)
        TYPES[name] = type(str(name), (DTO,), {
            '__slots__': slots, '_fields': tuple(fields)})


_build_types()
globals().update(TYPES)


def write_value(w, f, v):
    k = f['kind']
    if k == 'prim':
        getattr(w, _W[f['type']])(v)
    elif k == 'list':
        v = v or []
        w.count(len(v))
        m = _W[f['type']]
        for item in v:
            getattr(w, m)(item)
    elif k == 'listdto':
        v = v or []
        w.count(len(v))
        for item in v:
            write_dto(w, f['type'], item)
    else:
        write_dto(w, f['type'], v)


def read_value(r, f):
    k = f['kind']
    if k == 'prim':
        return getattr(r, _W[f['type']])()
    if k == 'list':
        m = _W[f['type']]
        return [getattr(r, m)() for _ in range(r.count())]
    if k == 'listdto':
        return [read_dto(r, f['type']) for _ in range(r.count())]
    return read_dto(r, f['type'])


def write_dto(w, type_name, v):
    """A null nested DTO would throw inside the client's own marshaller, so we
    substitute a zero-valued instance rather than emitting nothing."""
    cls = TYPES[type_name]
    if v is None:
        v = cls()
    for f in cls._fields:
        write_value(w, f, getattr(v, f['name']))


def read_dto(r, type_name):
    cls = TYPES[type_name]
    v = cls()
    for f in cls._fields:
        setattr(v, f['name'], read_value(r, f))
    return v


def encode_packet(packet_id, *args):
    """Serialise an RMI call body.  Positional args follow the client signature."""
    spec = PACKET_SPEC[packet_id]
    fields = spec['fields']
    if len(args) != len(fields):
        raise TypeError('%s(%d) takes %d args, got %d: %s' % (
            spec['name'], packet_id, len(fields), len(args),
            [f['name'] for f in fields]))
    w = Writer()
    for f, v in zip(fields, args):
        write_value(w, f, v)
    return bytes(w.buf)


def decode_packet(packet_id, body):
    """Deserialise an RMI call body into a dict keyed by the client's arg names."""
    spec = PACKET_SPEC.get(packet_id)
    if spec is None:
        raise KeyError('unknown packet id %d' % packet_id)
    r = Reader(body)
    out = {}
    for f in spec['fields']:
        out[f['name']] = read_value(r, f)
    out['_name'] = spec['name']
    out['_trailing'] = len(body) - r.pos
    return out


def packet_name(packet_id):
    s = PACKET_SPEC.get(packet_id)
    return '%s.%s' % (s['ns'], s['name']) if s else 'UNKNOWN(%d)' % packet_id
