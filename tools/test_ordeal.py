"""The Trial Tower, through the handlers.

    python tools/test_ordeal.py

See hc/game/ordeal.py.
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
    sandbox = tempfile.mkdtemp(prefix='hc-ordeal-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    os.environ['HC_ACCOUNTS_DIR'] = os.path.join(sandbox, 'accounts')
    os.makedirs(os.environ['HC_ACCOUNTS_DIR'])

    from hc.game.player import Player
    from hc.game import ordeal, state
    from hc.game.enums import CollectionType
    from hc.protocol.dto import TYPES
    from hc.net import HANDLERS
    import hc.handlers  # noqa: F401
    from hc.handlers import login as login_handler, not_open
    from tools.test_progression import FakeSession, call

    try:
        p = Player.create(900700, 'test-device-ord')
        s = FakeSession(p)
        party = [TYPES['NGPartyInfo'](SlotType=14, SlotIndex=0,
                                      UnitUID=p.d['units'][0]['uid'], SkillOnOff=1)]

        def start(did):
            return call(s, 30164, _DungeonID=did, _vecPartyInfo=party)

        def end(did, won=True):
            return call(s, 30165, _DungeonID=did, _ClearType=1 if won else 2,
                        _AutoEnable=False, _LogString='', _TotalPower=1000)

        print('\nthe tables')
        check('main tower 180 floors, side towers 80, group 6 ten',
              len(ordeal.floors(-1)) == 180 and len(ordeal.floors(0)) == 80
              and len(ordeal.floors(6)) == 10)
        check('daily limits from ConstValue: main none, side 3, tower 4 none',
              ordeal.daily_limit(-1) == -1 and ordeal.daily_limit(0) == 3
              and ordeal.daily_limit(4) == -1 and ordeal.daily_limit(6) == 3)
        check('StartOrdealTowerReq is handled, not refused',
              HANDLERS[30164].__module__.endswith('ordeal')
              and all(r[0] != 30164 for r in not_open.NOT_OPEN))

        print('\nclimbing')
        d = start(80002)
        check('floor 2 before floor 1 is refused (1157)', d['Error'] == 1157, d['Error'])
        d = start(80001)
        check('floor 1 starts and the party is kept',
              d['Error'] == 0 and d['_DungeonID'] == 80001
              and any(int(r['slot_type']) == 14 for r in p.parties()), d)
        d = end(80001, won=False)
        check('a loss clears nothing', d['Error'] == 0 and ordeal.clears(p, 80001) == 0)
        start(80001)
        d = end(80001)
        check('a win clears the floor (DungeonClearCount)', ordeal.clears(p, 80001) == 1)
        check('and pays nothing yet -- the clear reward is claimed',
              ordeal.claims(p, 80001) == 0)
        d = start(80001)
        check('a cleared floor cannot be played again (1158)', d['Error'] == 1158, d['Error'])
        d = call(s, 30166, _DungeonID=80001)
        gained = {(r.Type1, r.Type2) for r in d['_CheckInfo'].vecAddResourceInfo}
        check('claiming pays clearRewardGroup 10 (shards and gold) and counts it (58)',
              d['Error'] == 0 and {(13, 2), (0, -1)} <= gained and ordeal.claims(p, 80001) == 1,
              (d['Error'], gained))
        d = call(s, 30166, _DungeonID=80001)
        check('a second claim is refused', d['Error'] != 0 and ordeal.claims(p, 80001) == 1)
        d = call(s, 30166, _DungeonID=80002)
        check('an uncleared floor has nothing to claim', d['Error'] != 0)
        d = end(80002)
        check('an End with no battle started changes nothing', ordeal.clears(p, 80002) == 0)

        print('\nthe side towers\' daily limit')
        for _ in range(3):
            start(86001)
            d = end(86001, won=False)
        check('three plays counted and sent in the End Ack',
              [(i.GroupID, i.PlayCount) for i in d['_vecOrdealTowerPlayCount']]
              == [(-1, 2), (0, 3)] and d['_vecOrdealTowerPlayCount'][0].DailySeason
              == state.season_value(state.SEASON_DAILY), d['_vecOrdealTowerPlayCount'])
        d = start(86001)
        check('the fourth is refused (1354)', d['Error'] == 1354, d['Error'])
        tomorrow = datetime.utcnow() + timedelta(days=1)
        check('tomorrow the count starts again', ordeal.play_count(p, 0, tomorrow) == 0
              and ordeal.can_start(p, 86001, tomorrow) == 0)
        check('the main tower has no limit', ordeal.play_count(p, -1) == 2
              and ordeal.can_start(p, 80002) == 0)

        print('\nstandby reward')
        d = call(s, 30167)
        gained = {(r.Type1, r.Type2) for r in d['_CheckInfo'].vecAddResourceInfo}
        check('pays the highest main floor\'s waitRewardGroup (11: gold and Craft KIT)',
              d['Error'] == 0 and {(0, -1), (127, -1)} <= gained, (d['Error'], gained))
        d = call(s, 30167)
        check('once a day (1162 after)', d['Error'] == 1162, d['Error'])
        q = Player.create(900701, 'test-device-ord2')
        check('with no main floor cleared there is none', ordeal.wait_reward(q)[1] == 1162)

        print('\nthe shop')
        info = ordeal.shop_info(p)
        uids = [g.GoodsUID for g in info.vecGoodsInfo]
        check('tier 1 stock: 3 goods, the groupType-0 group\'s one first',
              len(uids) == 3 and ordeal.goods_row(uids[0])['shopGoodsGroup'] == '101', uids)
        check('open until the end of the week', info.tmOpenSeasonEnd > datetime.utcnow()
              and info.tmOpenSeasonEnd - info.tmOpenSeasonStart == timedelta(days=7))
        check('the same stock comes back (the roll is seeded)',
              [g.GoodsUID for g in ordeal.shop_info(p).vecGoodsInfo] == uids)
        q.d['ordeal_shop'] = None
        check('never-saved rolls repeat too', [g.GoodsUID for g in ordeal.shop_info(q).vecGoodsInfo]
              == [g.GoodsUID for g in ordeal.shop_info(q).vecGoodsInfo])
        g = ordeal.goods_row(uids[0])
        cost_t, cost = int(g['costType1']), int(g['costValue'])
        before = p.total_cash() if cost_t == 21 else p.get_resource(cost_t)
        d = call(s, 30169, _GoodsUID=uids[0])
        after = p.total_cash() if cost_t == 21 else p.get_resource(cost_t)
        check('buying charges the price and bumps BuyCount',
              d['Error'] == 0 and before - after == cost
              and [x.BuyCount for x in d['_ShopInfo'].vecGoodsInfo if x.GoodsUID == uids[0]] == [1],
              (d['Error'], before, after))
        d = call(s, 30169, _GoodsUID=uids[0])
        check('buyCount 1: a second one is refused', d['Error'] != 0)
        d = call(s, 30169, _GoodsUID=99999999)
        check('goods not in the stock are refused (2411)', d['Error'] == 2411, d['Error'])
        for r in ordeal.floors(-1)[:11]:
            p.set_collection(CollectionType.DungeonClearCount, 1, t2=int(r['dungeonID']))
        check('clearing floor 11 moves the shop to tier 11 and re-rolls it',
              ordeal.shop_tier(p) == 11
              and all(ordeal.goods_row(x.GoodsUID)['shopGoodsGroup'].startswith('11')
                      for x in ordeal.shop_info(p).vecGoodsInfo)
              and len(ordeal.shop_info(p).vecGoodsInfo) == 4)

        print('\nlogin')
        p.save()
        s2 = FakeSession(p)
        asyncio.run(login_handler.login(s2, {
            'DeviceID': 'test-device-ord', 'AccountID': p.account_id, 'AuthType': 20,
            'MarketID': 0, 'ClientVersion': 'test', 'statDeviceID': 'x', 'DeviceType': 0,
            'UserLanguage': 0, 'CheckKT': 0, 'ResourceVersion': 0, 'IsReconnect': False,
            '_name': 'LogInReq'}))
        ack01 = next(d for pid, d, *_ in s2.sent if pid == 40000)['ngAck']
        check('the shop and today\'s play counts go out at login',
              len(ack01.OrdealTowerShopInfo.vecGoodsInfo) == 4
              and ack01.OrdealTowerShopInfo.tmOpenSeasonEnd > datetime.utcnow()
              and [(i.GroupID, i.PlayCount) for i in ack01.vecOrdealTowerPlayCount]
              == [(-1, 2), (0, 3)], ack01.vecOrdealTowerPlayCount)
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
