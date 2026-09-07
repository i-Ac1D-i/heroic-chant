"""Entry point:  python -m hc.main"""
import argparse
import asyncio
import logging
import sys

from . import config
from .net import Server, HANDLERS
from .protocol.dto import PACKET_SPEC


def main(argv=None):
    ap = argparse.ArgumentParser(description='HC 1.2.389 private server')
    ap.add_argument('--host', default=config.BIND_HOST, help='bind address')
    ap.add_argument('--port', type=int, default=config.PORT)
    ap.add_argument('--public-host', default=config.PUBLIC_HOST,
                    help='address handed to the client for the game/match servers; '
                         'set this to your LAN IP when playing on a phone')
    ap.add_argument('--log-level', default=config.LOG_LEVEL)
    ap.add_argument('--log-file', default=None,
                    help='also append the log to this file; the console keeps '
                         'its copy, so a session can be watched and read back')
    ap.add_argument('--web-port', type=int, default=None,
                    help='port for the config dashboard (default: '
                         'settings.json dashboard.port, normally 8099)')
    ap.add_argument('--web-bind', default=None,
                    help='address for the dashboard; loopback unless a token is set')
    ap.add_argument('--no-web', action='store_true',
                    help='do not start the dashboard at all')
    args = ap.parse_args(argv)

    config.PUBLIC_HOST = args.public_host
    config.PORT = args.port

    handlers = [logging.StreamHandler()]
    if args.log_file:
        # Explicit UTF-8: the default on Windows is the ANSI code page, and a
        # single Korean string-table name in a log line is enough to kill the
        # server with a UnicodeEncodeError from inside the logging call.
        handlers.append(logging.FileHandler(args.log_file, encoding='utf-8'))
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format='%(asctime)s.%(msecs)03d %(levelname)-7s %(name)-12s %(message)s',
        datefmt='%H:%M:%S', handlers=handlers)

    log = logging.getLogger('hc')
    from . import handlers  # noqa: F401  -- registers everything
    if not args.no_web:
        # The dashboard is a convenience. A missing or broken one must never
        # stop the game server coming up -- that would turn a config-editor
        # problem into "the game does not work".
        try:
            from .webui import start as start_web
            start_web(bind=args.web_bind, port=args.web_port)
        except Exception:
            log.exception('dashboard failed to start; carrying on without it')
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
