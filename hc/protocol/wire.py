"""NGNet wire format.

Every constant and rule here was recovered by disassembling
``lib/arm64-v8a/libil2cpp.so`` from HC 1.2.389; the RVA of the
originating function is cited next to each rule.  See docs/PROTOCOL.md.
"""
import zlib
from datetime import datetime

HEADSIZE = 13                 # NGNet.BasicType.HEADSIZE
MAX_PACKET_SIZE = 262144      # NGNet.BasicType.MAX_PACKET_SIZE

# NGNet.RmiContext
RELIABLE, FAST_ENCRYP, RELIABLE_COMPRESS, FAST_ENCRYP_COMPRESS = 0, 1, 2, 3

# NGNet.BasicType control packet ids
HEART_BIT = -1
SC_HOSTID_INFO = -2
CS_HOSTID_RECV = -3
CS_HOSTID_RECONNECT = -4
SC_RECONNECT_SUCCESS = -5
SC_RECONNECT_FAIL = -6

# The client validates DateTime components on read (Marshal.Read @0x2271C04):
# year >= 1899, month 1..12, day 1..31, hour <= 24, minute <= 59, second <= 59.
EPOCH = datetime(2000, 1, 1)


class Writer:
    """Builds a packet body.  Mirrors NGNet.Message write side."""

    __slots__ = ('buf',)

    def __init__(self):
        self.buf = bytearray()

    # -- primitives (Message.Write @0x22701C4..0x2270710) -------------------
    def b(self, v):     self.buf.append(1 if v else 0)          # bool  -> 1 byte
    def u8(self, v):    self.buf.append(v & 0xFF)
    def i16(self, v):   self.buf += int(v).to_bytes(2, 'little', signed=True)
    def u16(self, v):   self.buf += int(v).to_bytes(2, 'little')
    def i32(self, v):   self.buf += int(v).to_bytes(4, 'little', signed=True)
    def u32(self, v):   self.buf += int(v).to_bytes(4, 'little')
    def i64(self, v):   self.buf += int(v).to_bytes(8, 'little', signed=True)
    def u64(self, v):   self.buf += int(v).to_bytes(8, 'little')

    def f32(self, v):
        import struct
        self.buf += struct.pack('<f', float(v))

    def f64(self, v):
        import struct
        self.buf += struct.pack('<d', float(v))

    def s(self, v):
        """int32 UTF-8 *byte* length, then the bytes (Message.Write @0x2270710)."""
        raw = ('' if v is None else str(v)).encode('utf-8')
        self.i32(len(raw))
        self.buf += raw

    def dt(self, v):
        """Six int16s: year, month, day, hour, minute, second (@0x2271D68)."""
        if v is None:
            v = EPOCH
        for part in (v.year, v.month, v.day, v.hour, v.minute, v.second):
            self.i16(part)

    def count(self, n):
        """List length prefix is int16, not int32 (@0x2270F24)."""
        self.i16(n)


class Reader:
    __slots__ = ('buf', 'pos')

    def __init__(self, buf, pos=0):
        self.buf, self.pos = buf, pos

    def _take(self, n):
        if self.pos + n > len(self.buf):
            raise EOFError('short read: want %d at %d of %d' % (n, self.pos, len(self.buf)))
        chunk = self.buf[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def b(self):    return self._take(1)[0] != 0
    def u8(self):   return self._take(1)[0]
    def i16(self):  return int.from_bytes(self._take(2), 'little', signed=True)
    def u16(self):  return int.from_bytes(self._take(2), 'little')
    def i32(self):  return int.from_bytes(self._take(4), 'little', signed=True)
    def u32(self):  return int.from_bytes(self._take(4), 'little')
    def i64(self):  return int.from_bytes(self._take(8), 'little', signed=True)
    def u64(self):  return int.from_bytes(self._take(8), 'little')

    def f32(self):
        import struct
        return struct.unpack('<f', self._take(4))[0]

    def f64(self):
        import struct
        return struct.unpack('<d', self._take(8))[0]

    def s(self):
        n = self.i32()
        if n < 0 or n > MAX_PACKET_SIZE:
            raise ValueError('bad string length %d' % n)
        return self._take(n).decode('utf-8', 'replace')

    def dt(self):
        y, mo, d, h, mi, se = (self.i16() for _ in range(6))
        try:
            return datetime(y, mo, d, h % 24, mi, se)
        except ValueError:
            return EPOCH

    def count(self):
        return self.i16()


def xor_crypt(buf, start, total_len, key):
    """NGNet.Crypto.XOREncrypt @0x1D4D1E0 -- symmetric, so it also decrypts.

    Rolling 32-bit XOR over little-endian dwords starting at ``start``
    (always 13, i.e. the body).  ``total_len`` is the *whole frame* length.
    The key index cycles over ``key``; a trailing run of fewer than 4 bytes
    is left in the clear.
    """
    if total_len - start < 4:
        return
    remaining = total_len + 4 - start
    ki, pos, n = 0, start, len(key)
    while True:
        v = int.from_bytes(buf[pos:pos + 4], 'little')
        k = key[ki] if ki < n else 0
        buf[pos:pos + 4] = ((k ^ v) & 0xFFFFFFFF).to_bytes(4, 'little')
        ki = ki + 1 if ki + 1 < n else 0
        remaining -= 4
        pos += 4
        if remaining <= 7:
            break


def build_frame(packet_id, seq, body, ctx=RELIABLE, key=None):
    """Assemble one wire frame.  Mirrors CNetClient.BeginSend @0x199D734."""
    frame = bytearray(HEADSIZE + len(body))
    frame[HEADSIZE:] = body
    frame[4:8] = int(packet_id).to_bytes(4, 'little', signed=True)
    frame[8:12] = int(seq).to_bytes(4, 'little', signed=True)
    frame[12] = ctx

    if ctx in (FAST_ENCRYP, FAST_ENCRYP_COMPRESS):
        # Encrypt first, then compress -- that is the client's order.
        xor_crypt(frame, HEADSIZE, len(frame), key or ())

    if ctx in (RELIABLE_COMPRESS, FAST_ENCRYP_COMPRESS):
        # ZipHelper.CompressToMessage @0x15D6DCC: body becomes
        # int32(original body length) + Ionic.Zlib deflate of the body.
        raw = bytes(frame[HEADSIZE:])
        comp = zlib.compress(raw, 1)
        frame = bytearray(frame[:HEADSIZE]) + len(raw).to_bytes(4, 'little') + comp

    frame[0:4] = len(frame).to_bytes(4, 'little')
    return bytes(frame)


def parse_frame(frame, key=None):
    """Inverse of build_frame.  Returns (packet_id, seq, ctx, body)."""
    size = int.from_bytes(frame[0:4], 'little')
    packet_id = int.from_bytes(frame[4:8], 'little', signed=True)
    seq = int.from_bytes(frame[8:12], 'little', signed=True)
    ctx = frame[12]
    buf = bytearray(frame[:size])

    if ctx in (RELIABLE_COMPRESS, FAST_ENCRYP_COMPRESS):
        orig_len = int.from_bytes(buf[HEADSIZE:HEADSIZE + 4], 'little')
        body = zlib.decompress(bytes(buf[HEADSIZE + 4:size]))
        if len(body) != orig_len:
            raise ValueError('decompressed length %d != declared %d' % (len(body), orig_len))
        buf = bytearray(buf[:HEADSIZE]) + body

    if ctx in (FAST_ENCRYP, FAST_ENCRYP_COMPRESS):
        xor_crypt(buf, HEADSIZE, len(buf), key or ())

    return packet_id, seq, ctx, bytes(buf[HEADSIZE:])
