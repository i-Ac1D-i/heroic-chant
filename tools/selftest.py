"""End-to-end check: drive a mock client through the real server.

Covers the framing (all four RmiContext modes), the handshake, the center
handshake, login, and a full stage clear with reward payout.  Run it before
pointing a real device at the server:

    python tools/selftest.py
"""
import asyncio
import os
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from hc.protocol import wire                                   # noqa: E402
from hc.protocol.dto import (TYPES, encode_packet, decode_packet,  # noqa: E402
                             PACKET_SPEC, DTO_SPEC)

FAILURES = []


def check(name, cond, detail=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (' -- ' + detail if detail else ''))
    if not cond:
        FAILURES.append(name)


# --------------------------------------------------------------------------
def test_wire():
    print('\nframing')
    key = (0x11223344, 0xdeadbeef, 0x0f0f0f0f)
    for ctx in (wire.RELIABLE, wire.FAST_ENCRYP,
                wire.RELIABLE_COMPRESS, wire.FAST_ENCRYP_COMPRESS):
        for n in (0, 1, 3, 4, 5, 7, 8, 11, 12, 100, 5000):
            body = bytes((i * 7 + n) & 0xFF for i in range(n))
            frame = wire.build_frame(30000, 1, body, ctx, key)
            size = int.from_bytes(frame[0:4], 'little')
            pid, seq, got_ctx, out = wire.parse_frame(frame, key)
            ok = (out == body and pid == 30000 and seq == 1
                  and got_ctx == ctx and size == len(frame))
            if not ok:
                check('ctx %d body %d' % (ctx, n), False,
                      'got %d bytes back' % len(out))
                return
    check('round-trip, 4 contexts x 11 body sizes', True)

    # XOR must be symmetric and must leave a sub-dword tail alone.
    buf = bytearray(wire.HEADSIZE + 7)
    buf[wire.HEADSIZE:] = b'ABCDEFG'
    orig = bytes(buf)
    wire.xor_crypt(buf, wire.HEADSIZE, len(buf), key)
    check('XOR leaves the trailing 3 bytes in the clear',
          bytes(buf[-3:]) == orig[-3:] and bytes(buf[13:17]) != orig[13:17])
    wire.xor_crypt(buf, wire.HEADSIZE, len(buf), key)
    check('XOR is its own inverse', bytes(buf) == orig)


def test_codecs():
    print('\ncodecs')
    check('180 DTOs, 859 packets loaded',
          len(DTO_SPEC) == 180 and len(PACKET_SPEC) == 859,
          '%d / %d' % (len(DTO_SPEC), len(PACKET_SPEC)))

    # NGPartyInfo declares UnitID but the client never serialises it.
    check('NGPartyInfo omits the unserialised UnitID field',
          [f['name'] for f in DTO_SPEC['NGPartyInfo']]
          == ['SlotType', 'SlotIndex', 'UnitUID', 'SkillOnOff'])

    party = [TYPES['NGPartyInfo'](SlotType=1, SlotIndex=i, UnitUID=100 + i)
             for i in range(3)]
    body = encode_packet(30009, 8256, party)
    # 4 (dungeonID) + 2 (int16 list count) + 3 * 20
    check('DungeonStartReq encodes to the expected length', len(body) == 4 + 2 + 60,
          '%d bytes' % len(body))
    check('list count prefix is int16',
          struct.unpack_from('<h', body, 4)[0] == 3)
    back = decode_packet(30009, body)
    check('DungeonStartReq round-trips',
          back['dungeonID'] == 8256 and len(back['vecPartyInfo']) == 3
          and back['vecPartyInfo'][2].UnitUID == 102 and back['_trailing'] == 0)

    # Every packet must encode from defaults and read back with nothing left over.
    bad = []
    for pid, spec in PACKET_SPEC.items():
        try:
            args = []
            for f in spec['fields']:
                if f['kind'] == 'prim':
                    args.append(TYPES['NGResourceInfo']().RegDate
                                if f['type'] == 'DateTime'
                                else ('' if f['type'] == 'string' else 0))
                elif f['kind'] in ('list', 'listdto'):
                    args.append([])
                else:
                    args.append(TYPES[f['type']]())
            blob = encode_packet(pid, *args)
            if decode_packet(pid, blob)['_trailing'] != 0:
                bad.append(pid)
        except Exception as exc:                       # noqa: BLE001
            bad.append('%d(%s)' % (pid, exc))
    check('all 859 packets encode+decode cleanly', not bad, str(bad[:5]))


# --------------------------------------------------------------------------
class MockClient(object):
    """Speaks the client half of the protocol over a real socket."""

    def __init__(self, reader, writer):
        self.r, self.w = reader, writer
        self.key = ()
        self.host_id = None
        self.seq = 0

    async def recv(self):
        head = await self.r.readexactly(4)
        size = int.from_bytes(head, 'little')
        frame = head + await self.r.readexactly(size - 4)
        return wire.parse_frame(frame, self.key)

    async def send(self, pid, *args):
        self.seq += 1
        body = encode_packet(pid, *args)
        self.w.write(wire.build_frame(pid, self.seq, body, wire.RELIABLE, self.key))
        await self.w.drain()

    async def handshake(self):
        pid, _, _, body = await self.recv()
        assert pid == wire.SC_HOSTID_INFO, pid
        r = wire.Reader(body)
        self.host_id = r.i64()
        self.key = (r.u32(), r.u32(), r.u32())
        self.seq += 1
        self.w.write(wire.build_frame(wire.CS_HOSTID_RECV, self.seq, b'', 0, self.key))
        await self.w.drain()

    async def expect(self, pid, skip_heartbeats=True, timeout=15):
        """Time out rather than hang: a handler that throws leaves the client
        waiting forever, and a silent hang says much less than a failure."""
        while True:
            try:
                got, _, _, body = await asyncio.wait_for(self.recv(), timeout)
            except asyncio.TimeoutError:
                raise AssertionError(
                    'timed out waiting for packet %d -- the handler probably '
                    'raised; check the server log' % pid)
            if skip_heartbeats and got == wire.HEART_BIT:
                continue
            assert got == pid, 'wanted %d, got %d' % (pid, got)
            return decode_packet(pid, body)


async def run_session(port):
    print('\nlive session')
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    c = MockClient(reader, writer)
    await c.handshake()
    check('handshake: hostID + 3 XOR key words',
          c.host_id == 1 and len(c.key) == 3)

    await c.send(10000, 'selftest-device', 20, 1, 10)
    ack = await c.expect(20000)
    check('GetServerGroupInfoAck', ack['Error'] == 0
          and len(ack['vecServerGroupInfo']) == 1
          and len(ack['vecAccountInfo']) == 1)
    account_id = ack['vecAccountInfo'][0].AccountID

    await c.send(10001, 1)
    ack = await c.expect(20001)
    check('GetConnectGameServerInfoAck points somewhere', bool(ack['HostName']))

    await c.send(30000, 'selftest-device', account_id, 20, 0,
                 '1.2.389', 'selftest-device', 1, 10, 0, 22, False)
    a1 = await c.expect(40000)
    check('LogInAck01 carries the profile',
          a1['ngAck'].Error == 0 and a1['ngAck'].Nickname
          and a1['ngAck'].IsJoin is True)
    for pid in (40001, 40002, 40003, 40004):
        await c.expect(pid)
    large = await c.expect(40005)
    n_units = len(large['ngAck'].vecUnit)
    n_res = len(large['ngAck'].vecResource)
    check('LoginAckLargeData carries roster + wallet',
          n_units > 100 and n_res > 5 and large['ngAck'].bEndPacket is True,
          '%d units, %d resources' % (n_units, n_res))

    def wallet(ack_obj):
        return {(r.Type1, r.Type2, r.Type3): r.Value1
                for r in ack_obj.vecResource}
    before = wallet(large['ngAck'])
    gold_before = before.get((0, -1, -1), 0)
    stamina_before = before.get((129, -1, -1), 0)

    party = [TYPES['NGPartyInfo'](SlotType=1, SlotIndex=0,
                                 UnitUID=large['ngAck'].vecUnit[0].UID)]
    await c.send(30009, 2, party)
    ack = await c.expect(40014)
    check('DungeonStartAck accepted stage 2', ack['Error'] == 0 and ack['dungeonID'] == 2)

    await c.send(30010, 2, 3, True, 7, '')
    ack = await c.expect(40015)
    after = {(r.Type1, r.Type2, r.Type3): r.Value1
             for r in ack['_CheckInfo'].vecAddResourceInfo}
    check('DungeonEndAck paid out', ack['Error'] == 0 and bool(after))
    check('stage cost was deducted from stamina',
          after.get((129, -1, -1), stamina_before) < stamina_before,
          'was %d, now %s' % (stamina_before, after.get((129, -1, -1))))
    check('gold went up on clear',
          after.get((0, -1, -1), gold_before) > gold_before,
          'was %d, now %s' % (gold_before, after.get((0, -1, -1))))
    check('unlocked the next stage', len(ack['_vecDungeonOpenEnable']) > 0)

    await c.send(30001, large['ngAck'].vecUnit[0].UID)
    ack = await c.expect(40006)
    check('UnitLevelUpAck', ack['Error'] == 0
          and len(ack['_CheckInfo'].vecChangeUnitInfo) == 1
          and ack['_CheckInfo'].vecChangeUnitInfo[0].Level == 2)

    writer.close()


async def amain():
    import logging
    from hc import config
    from hc.net import Server
    import hc.handlers                                          # noqa: F401
    from hc.game import player as player_mod

    logging.getLogger('hc').setLevel(logging.ERROR)
    tmp = tempfile.mkdtemp(prefix='hc-selftest-')
    player_mod.ACCOUNTS_DIR = tmp
    import hc.handlers.center as center_mod
    center_mod.ACCOUNTS_DIR = tmp
    center_mod.INDEX = os.path.join(tmp, 'index.json')

    port = 21099
    config.PUBLIC_HOST, config.PORT = '127.0.0.1', port
    srv = Server('127.0.0.1', port)
    task = asyncio.ensure_future(srv.serve())
    await asyncio.sleep(0.3)
    try:
        await run_session(port)
    finally:
        task.cancel()
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    import logging
    logging.basicConfig(level=logging.WARNING)
    test_wire()
    test_codecs()
    asyncio.run(amain())
    print('\n%s' % ('ALL CHECKS PASSED' if not FAILURES
                    else '%d FAILED: %s' % (len(FAILURES), ', '.join(FAILURES))))
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
