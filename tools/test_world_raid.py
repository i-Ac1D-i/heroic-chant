"""World Raid single play, through the handlers.

    python tools/test_world_raid.py

See hc/game/world_raid.py.
"""
import asyncio
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
    sandbox = tempfile.mkdtemp(prefix='hc-wraid-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    os.environ['HC_ACCOUNTS_DIR'] = os.path.join(sandbox, 'accounts')
    os.makedirs(os.environ['HC_ACCOUNTS_DIR'])

    from hc.game.player import Player
    from hc.game import world_raid as wr
    from hc.protocol.dto import TYPES
    from hc.net import HANDLERS
    import hc.handlers  # noqa: F401
    from hc.handlers import login as login_handler
    from tools.test_progression import FakeSession, call

    try:
        p = Player.create(901200, 'test-device-wraid')
        s = FakeSession(p)
        party = [TYPES['NGPartyInfo'](SlotType=20, SlotIndex=1,
                                      UnitUID=p.d['units'][0]['uid'], SkillOnOff=0)]

        def start(did):
            return call(s, 30212, _DungeonID=did, _vecPartyInfo=party, _RoomIndex=0)

        def end(did, kills):
            return call(s, 30213, _DungeonID=did, _vecPartyAccountID=[p.account_id],
                        _ClearType=1, _TurnCnt=10, _AttackCnt=20, _TotalPower=1000,
                        _MonsterKillCount=kills, _LogString='')

        print('\nrewards')
        full = dict(((t1, t2), v) for t1, t2, t3, v in wr.tier_rewards(120101, 35))
        check('the four tiers add up to the rewardClear preview (21 coins, 9000 gold, a box)',
              full == {(119, 8): 21, (0, -1): 9000, (48, 800): 1}, full)
        check('26 kills reach three tiers, 9 none',
              dict(((t1, t2), v) for t1, t2, t3, v in wr.tier_rewards(120101, 26))
              == {(119, 8): 21, (0, -1): 9000} and wr.tier_rewards(120101, 9) == [])

        print('\nstarting')
        check('WorldRaidGameStartReq is handled', 30212 in HANDLERS and 30213 in HANDLERS)
        d = start(120102)
        check('raid 2 is closed before raid 1 is cleared (1207)', d['_Error'] == 1207, d['_Error'])
        p.d['resources'].pop('157:-1:-1', None)
        d = start(120101)
        check('without a World Raid ticket it is refused (1200)', d['_Error'] == 1200, d['_Error'])
        p.add_resource(157, 3)
        d = start(120101)
        check('with one it starts, ticket not spent yet',
              d['_Error'] == 0 and p.get_resource(157) == 3, d['_Error'])

        print('\nending')
        d = end(120101, 5)
        check('5 kills: short of the clear, nothing paid or spent',
              not d['_vecReward'] and p.get_resource(157) == 3)
        check('but the best kill count is kept (group 1: 5)',
              [(x.first, x.second) for x in d['vecWorldRaidClearInfo']] == [(1, 5)])
        start(120101)
        gold = p.get_resource(0)
        d = end(120101, 30)
        check('30 kills: three tiers paid, one ticket spent',
              p.get_resource(0) == gold + 9000 and p.get_resource(157) == 2, p.get_resource(0) - gold)
        check('plus the daily bonus box, count 1 of 3',
              [(r.Type1, r.Type2) for r in d['_vecDailyReward']] == [(48, 804)]
              and [(x.GropuID, x.RewardCount) for x in
                   d['_CheckInfo'].vecChangeWorldRaidDailyReward] == [(2001, 1)])
        check('raid 2 is open now', wr.is_open(p, 120102) and start(120102)['_Error'] == 0)
        end(120102, 0)
        d = end(120101, 30)
        check('an End with no run started pays nothing', not d['_vecReward'])

        print('\nlogin')
        p.save()
        s2 = FakeSession(p)
        asyncio.run(login_handler.login(s2, {
            'DeviceID': 'test-device-wraid', 'AccountID': p.account_id, 'AuthType': 20,
            'MarketID': 0, 'ClientVersion': 'test', 'statDeviceID': 'x', 'DeviceType': 0,
            'UserLanguage': 0, 'CheckKT': 0, 'ResourceVersion': 0, 'IsReconnect': False,
            '_name': 'LogInReq'}))
        ack01 = next(d for pid, d, *_ in s2.sent if pid == 40000)['ngAck']
        check('clear info and the daily count go out at login',
              [(x.first, x.second) for x in ack01.vecWorldRaidClearInfo] == [(1, 30)]
              and [(x.GropuID, x.RewardCount) for x in ack01.vecWorldRaidDailyReward] == [(2001, 1)],
              (ack01.vecWorldRaidClearInfo, ack01.vecWorldRaidDailyReward))
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
