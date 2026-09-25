"""City Search's "Speed Acquired" fast reward, driven through the real handlers.

    python tools/test_city_search.py

GetDungeonAutoPlayFastRewardReq (30268) buys `count` lots of the farm's loot;
fastRewardTime prices the 1st..10th purchase of the day.  See hc/game/afk.py.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s  %s%s' % ('PASS' if cond else 'FAIL', name,
                          '' if cond else ('  -- ' + str(detail))))


def main():
    sandbox = tempfile.mkdtemp(prefix='hc-citysearch-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    accounts = os.path.join(sandbox, 'accounts')
    os.makedirs(accounts)

    from hc.game import player as player_mod
    player_mod.ACCOUNTS_DIR = accounts
    from hc.game.player import Player
    from hc.game import afk
    from hc.game.enums import ResourceType
    import hc.handlers  # noqa: F401
    from tools.test_progression import FakeSession, call

    try:
        p = Player.create(900700, 'test-device-citysearch')
        s = FakeSession(p)
        # A few cleared stages so City Search has a stage to farm.
        for did in range(1, 11):
            p.mark_cleared(did, 7)
        gold, cash = ResourceType.Gold, p.CASH
        p.d['resources'] = {}
        p.add_resource(gold, 5000)
        p.add_resource(cash, 1000)

        print('\nthe City Search screen')
        d = call(s, 30008)
        check('the day starts with no purchases', d['_fastReward'].FastRewardRecvCount == 0,
              d['_fastReward'])

        print('\nbuying fast rewards')
        # The farm's loot is random; pin it so the costs can be checked exactly,
        # and record how many minutes of it were asked for.
        asked = []
        real_roll = afk.roll
        afk.roll = lambda did, hours, rng=None: asked.append(hours) or [(99, -1, -1, 5)]
        try:
            d = call(s, 30268, _count=1)
            check('the first purchase works', d['Error'] == 0, d['Error'])
            check('it costs 1 000 gold', p.get_resource(gold) == 4000, p.get_resource(gold))
            check('the count comes back as 1', d['_fastReward'].FastRewardRecvCount == 1)
            check('it pays two hours of the farm', asked == [2.0], asked)
            check('and the loot arrives', p.get_resource(99) == 5)

            cash_before = p.total_cash()
            d = call(s, 30268, _count=3)
            check('three at once works', d['Error'] == 0, d['Error'])
            check('six hours of loot for three', asked[-1] == 6.0, asked)
        finally:
            afk.roll = real_roll

        check('they cost rows 2, 3 and 4: 10 + 20 + 20 cash',
              p.total_cash() == cash_before - 50, (cash_before, p.total_cash()))
        check('the count is now 4', d['_fastReward'].FastRewardRecvCount == 4)

        d = call(s, 30268, _count=7)
        check('going past the 10th of the day is refused', d['Error'] != 0)
        check('and charges nothing', p.total_cash() == cash_before - 50)

        p.d['resources']['19:-1:-1'] = 0          # Cash
        p.d['resources']['20:-1:-1'] = 0          # EventCash
        d = call(s, 30268, _count=1)
        check('without the cash it is refused', d['Error'] != 0)
        check('and the count does not move', afk.fast_state(p)['count'] == 4)

        print('\na new day')
        tomorrow = datetime.utcnow() + timedelta(days=1)
        check('the count resets', afk.fast_state(p, tomorrow)['count'] == 0)
        check('and the first purchase is gold again',
              afk.fast_cost(p, 1, tomorrow) == [(gold, -1, 1000)],
              afk.fast_cost(p, 1, tomorrow))
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
