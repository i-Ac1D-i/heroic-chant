"""Entry point:  python -m hc.main"""
import argparse
import asyncio
import logging
import sys

from . import config
from .net import Server, HANDLERS
from .protocol.dto import PACKET_SPEC


def main(argv=None):
    ap = argparse.ArgumentParser(description='Hero Cantare 1.2.389 private server')
    ap.add_argument('--host', default=config.BIND_HOST, help='bind address')
    ap.add_argument('--port', type=int, default=config.PORT)
    ap.add_argument('--public-host', default=config.PUBLIC_HOST,
                    help='address handed to the client for the game/match servers; '
                         'set this to your LAN IP when playing on a phone')
    ap.add_argument('--log-level', default=config.LOG_LEVEL)
    args = ap.parse_args(argv)

    config.PUBLIC_HOST = args.public_host
    config.PORT = args.port

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format='%(asctime)s %(levelname)-7s %(name)-12s %(message)s',
        datefmt='%H:%M:%S')

    log = logging.getLogger('hc')
    from . import handlers  # noqa: F401  -- registers everything
    log.info('protocol: %d packets, %d handlers implemented',
             len(PACKET_SPEC), len(HANDLERS))
    log.info('clients will be told to connect to %s:%d', config.PUBLIC_HOST, config.PORT)

    try:
        asyncio.run(Server(args.host, args.port).serve())
    except KeyboardInterrupt:
        log.info('shutting down')
    return 0


if __name__ == '__main__':
    sys.exit(main())
