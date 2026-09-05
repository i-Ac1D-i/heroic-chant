"""TCP transport and RMI dispatch.

One listener serves every role the client talks to (center, game, match):
the packet-id ranges are disjoint, and the client opens a fresh connection
for each, so a single unified dispatcher is enough and avoids a second port.
"""
import asyncio
import logging
import os
import struct
import time

from .protocol import wire
from .protocol.dto import decode_packet, encode_packet, packet_name

log = logging.getLogger('hc.net')

HANDLERS = {}


def handler(packet_id):
    """Register a coroutine as the handler for one C2S packet id."""
    def deco(fn):
        HANDLERS[packet_id] = fn
        return fn
    return deco


class Session(object):
    def __init__(self, reader, writer, server):
        self.reader, self.writer, self.server = reader, writer, server
        self.peer = writer.get_extra_info('peername')
        self.host_id = server.next_host_id()
        # The client adopts whatever key we hand it in SC_HOSTID_INFO
        # (NGNet.Crypto.XOR_KEY starts empty), so we simply choose one.
        self.key = tuple(struct.unpack('<3I', os.urandom(12)))
        self.seq_send = 0
        self.account = None
        self.player = None
        self.closed = False
        self._lock = asyncio.Lock()

    # -- sending -----------------------------------------------------------
    async def send_raw(self, packet_id, body, ctx=wire.RELIABLE):
        async with self._lock:
            self.seq_send += 1          # CNetClient.BeginSend pre-increments
            frame = wire.build_frame(packet_id, self.seq_send, body, ctx, self.key)
            self.writer.write(frame)
            await self.writer.drain()

    async def send(self, packet_id, *args):
        """Encode and send an S2C RMI call by packet id."""
        body = encode_packet(packet_id, *args)
        log.debug('S2C %s (%d bytes)', packet_name(packet_id), len(body))
        await self.send_raw(packet_id, body)

    async def send_hostid_info(self):
        """SC_HOSTID_INFO -- always plaintext; carries the session id + XOR key."""
        w = wire.Writer()
        w.i64(self.host_id)
        for k in self.key:
            w.u32(k)
        await self.send_raw(wire.SC_HOSTID_INFO, bytes(w.buf), wire.RELIABLE)

    async def heartbeat(self, value=None):
        w = wire.Writer()
        w.i64(int(time.time() * 1000) if value is None else value)
        await self.send_raw(wire.HEART_BIT, bytes(w.buf))

    # -- receiving ---------------------------------------------------------
    async def _read_frame(self):
        head = await self.reader.readexactly(4)
        size = int.from_bytes(head, 'little')
        if size < wire.HEADSIZE or size > wire.MAX_PACKET_SIZE:
            raise ValueError('bogus packet size %d' % size)
        return head + await self.reader.readexactly(size - 4)

    async def run(self):
        await self.send_hostid_info()
        hb = asyncio.ensure_future(self._heartbeat_loop())
        try:
            while True:
                frame = await self._read_frame()
                pid, seq, ctx, body = wire.parse_frame(frame, self.key)
                await self._dispatch(pid, body)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception:
            log.exception('session %s failed', self.peer)
        finally:
            hb.cancel()
            self.closed = True
            self.writer.close()
            log.info('disconnected %s', self.peer)

    async def _heartbeat_loop(self):
        try:
            while not self.closed:
                await asyncio.sleep(5)
                await self.heartbeat()
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _brief(args, limit=300):
        """Decoded packet args, short enough to sit on a log line.

        Some requests carry the whole roster (TotalUnitPowerInfoReq is 1.6 KB
        of unit ids), so this is truncated rather than dumped.
        """
        text = repr({k: v for k, v in args.items() if k != '_trailing'})
        return text if len(text) <= limit else text[:limit] + '...'

    async def _dispatch(self, pid, body):
        if pid < 0:
            return await self._control(pid, body)
        fn = HANDLERS.get(pid)
        try:
            args = decode_packet(pid, body)
        except Exception:
            log.exception('failed to decode %s', packet_name(pid))
            return
        if args.get('_trailing'):
            log.warning('%s: %d trailing bytes -- codec may be wrong',
                        packet_name(pid), args['_trailing'])
        if fn is None:
            # Decode it anyway. spec.json knows all 859 layouts, and the
            # arguments are the whole point of a no-handler line -- they say
            # what the client was actually asking for.
            log.warning('C2S %s -- no handler (%d body bytes) %s',
                        packet_name(pid), len(body), self._brief(args))
            return
        log.info('C2S %s', packet_name(pid))
        log.debug('      args %s', self._brief(args))
        try:
            await fn(self, args)
        except Exception:
            # A handler bug must not take the connection down with it -- the
            # client cannot recover from a mid-session disconnect and will sit
            # on a black screen.
            log.exception('handler for %s failed', packet_name(pid))

    async def _control(self, pid, body):
        if pid == wire.HEART_BIT:
            return                       # client's echo of our ping
        if pid == wire.CS_HOSTID_RECV:
            log.info('handshake complete with %s (hostID %d)', self.peer, self.host_id)
            return
        if pid == wire.CS_HOSTID_RECONNECT:
            await self.send_raw(wire.SC_RECONNECT_SUCCESS, b'')
            return
        log.warning('unhandled control packet %d', pid)


class Server(object):
    def __init__(self, host='0.0.0.0', port=21010):
        self.host, self.port = host, port
        self._host_id = 0

    def next_host_id(self):
        self._host_id += 1
        return self._host_id

    async def _client(self, reader, writer):
        s = Session(reader, writer, self)
        log.info('connected %s', s.peer)
        await s.run()

    async def serve(self):
        # On Windows SO_REUSEADDR lets a second process bind a port that is
        # already in use and silently steal connections, so a stale server from
        # a killed run keeps answering and the new one looks broken.  Bind
        # exclusively there so a clash fails loudly instead.
        srv = await asyncio.start_server(
            self._client, self.host, self.port,
            reuse_address=None if os.name == 'nt' else True)
        log.info('listening on %s:%d', self.host, self.port)
        async with srv:
            await srv.serve_forever()
