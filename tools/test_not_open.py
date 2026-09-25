"""Modes this server does not run yet answer with an error instead of silence.

    python tools/test_not_open.py

Each request in hc/handlers/not_open.py must get its own Ack, with the error
code first, marshalled cleanly, and change nothing in the save.
"""
import copy
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s  %s%s' % ('PASS' if cond else 'FAIL', name,
                          '' if cond else ('  -- ' + str(detail))))


def main():
    sandbox = tempfile.mkdtemp(prefix='hc-notopen-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    accounts = os.path.join(sandbox, 'accounts')
    os.makedirs(accounts)

    from hc.game import player as player_mod
    player_mod.ACCOUNTS_DIR = accounts
    from hc.game.player import Player
    from hc.protocol.dto import PACKET_SPEC
    import hc.handlers  # noqa: F401
    from hc.net import HANDLERS
    from hc.handlers import not_open
    from tools.test_progression import FakeSession, call

    try:
        p = Player.create(900900, 'test-device-notopen')
        s = FakeSession(p)
        before = copy.deepcopy(p.d)
        print()
        for req, ack, err, label in not_open.NOT_OPEN:
            if not HANDLERS[req].__name__.startswith('not_open_'):
                check('%s now has a real handler (skipped)' % label, True)
                continue
            args = {f['name']: None for f in PACKET_SPEC[req]['fields']}
            args = {k: ([] if k.startswith('vec') or k.startswith('_vec') else 0)
                    for k in args}
            d = call(s, req, **args)
            sent_id = s.sent[-1][0]
            first = PACKET_SPEC[ack]['fields'][0]['name']
            check('%s: answered with its own Ack and error %d' % (label, err),
                  sent_id == ack and d[first] == err, (sent_id, d.get(first)))
        check('nothing in the save changed', p.d == before)
    finally:
        os.environ.pop('HC_SETTINGS', None)
        shutil.rmtree(sandbox, ignore_errors=True)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        print('FAILED: ' + ', '.join(FAIL))
        return 1
    print('ALL CHECKS PASSED (%d)' % len(PASS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
