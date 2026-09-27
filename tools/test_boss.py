"""The Advent Boss, through the handlers.

    python tools/test_boss.py

See hc/game/boss.py.
"""
import asyncio
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
    sandbox = tempfile.mkdtemp(prefix='hc-boss-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    os.environ['HC_ACCOUNTS_DIR'] = os.path.join(sandbox, 'accounts')
    os.makedirs(os.environ['HC_ACCOUNTS_DIR'])

    from hc.game.player import Player
    from hc.game import boss
    from hc.protocol.dto import TYPES
    from hc.net import HANDLERS
    import hc.handlers  # noqa: F401
    from hc.handlers import login as login_handler, not_open
    from tools.test_progression import FakeSession, call

    try:
        p = Player.create(900800, 'test-device-boss')
        s = FakeSession(p)
        party = [TYPES['NGPartyInfo'](SlotType=16, SlotIndex=0,
                                      UnitUID=p.d['units'][0]['uid'], SkillOnOff=1)]

        def start(did):
            return call(s, 30179, _DungeonID=did, _SelectParty=0, _vecPartyInfo=party)

        def end(did, won=True, new=False):
            args = dict(_DungeonID=did, _SelectParty=0, _ClearType=1 if won else 2,
                        _ImmediatelyClearCount=0, _AutoEnable=False, _LogString='',
                        _TurnCnt=5, _AttackCnt=10, _TotalPower=1000)
            if new:
                args['_snapshot'] = ''
            return call(s, 30344 if new else 30180, **args)

        print('\nstarting')
        check('StartBossDungeonReq is handled, not refused',
              HANDLERS[30179].__module__.endswith('boss')
              and all(r[0] != 30179 for r in not_open.NOT_OPEN))
        d = start(90002)
        check('floor 2 before floor 1 is refused', d['Error'] != 0, d['Error'])
        stamina = p.get_resource(129)
        d = start(90001)
        check('floor 1 starts and charges its 10 stamina, synced in the Ack',
              d['Error'] == 0 and p.get_resource(129) == stamina - 10
              and any(r.Type1 == 129 for r in d['_CheckInfo'].vecAddResourceInfo),
              (d['Error'], p.get_resource(129)))
        p.d['resources']['129:-1:-1'] = 5
        d = start(90001)
        check('without the stamina it is refused, nothing charged',
              d['Error'] == 2 and p.get_resource(129) == 5, d['Error'])
        p.d['resources']['129:-1:-1'] = 999

        print('\nending')
        start(90001)
        d = end(90001, won=False)
        check('a loss pays nothing and clears nothing',
              d['Error'] == 0 and not boss.cleared(p, 90001) and not d['_vecDailyReward'])
        start(90001)
        d = end(90001)
        check('a win clears the floor', d['Error'] == 0 and boss.cleared(p, 90001))
        check('and pays the floor\'s daily bonus rows (material + box), count 1 of 3',
              sorted((r.Type1, r.Type2) for r in d['_vecDailyReward']) == [(48, 363), (131, 2)]
              and [(i.GropuID, i.RewardCount) for i in
                   d['_CheckInfo'].vecChangeBossDungeonDailyReward] == [(16, 1)],
              (d['_vecDailyReward'], d['_CheckInfo'].vecChangeBossDungeonDailyReward))
        d = start(90002)
        check('floor 2 opens', d['Error'] == 0)
        end(90002)
        start(90002)
        d = end(90002)
        check('the third win of the day still pays', bool(d['_vecDailyReward'])
              and boss.daily_count(p, 16) == 3)
        start(90002)
        d = end(90002)
        check('the fourth does not (3 a day)', not d['_vecDailyReward']
              and boss.daily_count(p, 16) == 3)
        check('tomorrow the count is back to 0',
              boss.daily_count(p, 16, datetime.utcnow() + timedelta(days=1)) == 0)
        d = end(90002)
        check('an End with no battle started pays nothing', not d['_vecDailyReward'])

        print('\nthe new boss')
        check('group 4 costs a Whale Boss ticket on top',
              (181, -1, 1) in boss.cost(93010) and (129, -1, 10) not in boss.cost(93010))
        p.add_resource(181, 1)
        d = start(93010)
        check('it starts and takes the ticket', d['Error'] == 0 and p.get_resource(181) == 0,
              d['Error'])
        d = end(93010, new=True)
        check('EndNewBossDungeonReq is answered with EndBossDungeonAck',
              s.sent[-1][0] == 40196 and d['Error'] == 0 and boss.cleared(p, 93010))

        print('\nlogin')
        p.d['boss_daily_reward'] = {'16': 2}         # an old save: a bare count, no day
        check('an old bare count reads as not today', boss.daily_count(p, 16) == 0)
        boss.take_daily(p, 90001)
        p.save()
        s2 = FakeSession(p)
        asyncio.run(login_handler.login(s2, {
            'DeviceID': 'test-device-boss', 'AccountID': p.account_id, 'AuthType': 20,
            'MarketID': 0, 'ClientVersion': 'test', 'statDeviceID': 'x', 'DeviceType': 0,
            'UserLanguage': 0, 'CheckKT': 0, 'ResourceVersion': 0, 'IsReconnect': False,
            '_name': 'LogInReq'}))
        ack01 = next(d for pid, d, *_ in s2.sent if pid == 40000)['ngAck']
        check('today\'s counts go out at login, every group present',
              sorted((i.GropuID, i.RewardCount) for i in ack01.vecBossDungeonDailyReward)
              == [(16, 1), (2001, 0)], ack01.vecBossDungeonDailyReward)
    finally:
        os.environ.pop('HC_SETTINGS', None)
        os.environ.pop('HC_ACCOUNTS_DIR', None)
        shutil.rmtree(sandbox, ignore_errors=True)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        print('FAILED: ' + ', '.join(FAIL))
        return 1
    print('ALL CHECKS PASSED (%d)' % len(PASS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
