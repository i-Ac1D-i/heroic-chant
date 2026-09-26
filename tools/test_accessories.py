"""Accessories (earrings / necklaces) and the Equipment Summon, through the handlers.

    python tools/test_accessories.py

See hc/game/accessories.py.
"""
import asyncio
import os
import random
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
    sandbox = tempfile.mkdtemp(prefix='hc-accessories-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    os.environ['HC_ACCOUNTS_DIR'] = os.path.join(sandbox, 'accounts')
    os.makedirs(os.environ['HC_ACCOUNTS_DIR'])

    from hc.game.player import Player
    from hc.game import accessories as acc, rewards, state
    from hc.data.tables import TABLES
    import hc.handlers  # noqa: F401
    from hc.handlers import login as login_handler
    from tools.test_progression import FakeSession, call

    try:
        print('\nrolled stats')
        rng = random.Random(3)
        bad = []
        for acc_id, count in ((200001, 1), (200002, 1), (200003, 2), (200004, 3),
                              (200005, 4), (210005, 4)):
            for _ in range(60):
                stats = acc.roll_stats(acc_id, rng=rng)
                slots = [s[0] for s in stats]
                types = [s[1] for s in stats]
                if slots != list(range(1, count + 1)) or len(set(types)) != len(types):
                    bad.append((acc_id, stats))
                for _slot, stat, value in stats:
                    lo, hi = (float(x) for x in acc._range(acc.grade(acc_id), stat))
                    if not lo <= value <= hi:
                        bad.append((acc_id, stat, value, lo, hi))
        check('grades 1-5 roll 1/1/2/3/4 distinct stats, slots from 1, within range',
              not bad, bad[:3])
        check('materials (itemType 16) have no stats', acc.roll_stats(220003, rng=rng) == [])

        print('\nthe Equipment Summon pool')
        pulls = acc.roll(1, 4000, random.Random(5))
        share = sum(1 for t1, _t2, _n in pulls if t1 == 137) / len(pulls)
        check('4000 pulls: about a quarter accessories, the rest gear',
              0.2 < share < 0.3 and {t1 for t1, _t2, _n in pulls} == {5, 137}, share)

        p = Player.create(900600, 'test-device-acc')
        s = FakeSession(p)
        p.d['resources'] = {}
        d = call(s, 30205, AcceGachaGroupID=102)
        check('a 10x summon with nothing to pay is refused', d['Error'] != 0)

        p.add_resource(152, 10, 4)                         # Equipment Summon Tickets
        before = len(p.accessories())
        d = call(s, 30205, AcceGachaGroupID=102)
        got = d['_vecReward']
        new = d['_CheckInfo'].vecAddAccessoryInfo or []
        minted = len(p.accessories()) - before
        check('10 tickets buy a 10x summon', d['Error'] == 0 and p.get_resource(152, 4) == 0
              and len(got) == 10, (d['Error'], p.get_resource(152, 4), len(got)))
        check('each accessory pulled is minted and announced',
              minted == sum(1 for r in got if r.Type1 == 137) == len(new), (minted, len(new)))
        check('gear pulled lands in the wallet',
              all(p.get_resource(5, r.Type2) >= 1 for r in got if r.Type1 == 5))

        p.add_resource(19, 1000)                           # diamonds
        d = call(s, 30205, AcceGachaGroupID=101)
        check('without tickets a single costs 80 diamonds',
              d['Error'] == 0 and p.total_cash() == 920, p.total_cash())

        from hc.game import events
        ev = sorted(events.equip_events(), key=lambda e: e.UID)
        check('the Equipment Summon is announced: type-105 events for the 1x and 10x groups',
              [(e.ID, int(e.Arg1), int(acc.gacha_row(int(e.Arg1))['GachaCount'])) for e in ev]
              == [(105, 101, 1), (105, 102, 10)], ev)

        print('\nfrom rewards, mail and old saves')
        before = len(p.accessories())
        rewards.grant(p, [(137, 200005, -1, 2)])
        sync = state.resource_sync(p)
        check('a reward of 137 mints real accessories and the next sync says so',
              len(p.accessories()) == before + 2
              and [x.ID for x in sync.vecAddAccessoryInfo] == [200005, 200005])
        p.d['resources']['137:210004:-1'] = 3              # what the dashboard used to write
        check('wallet rows from old saves become accessories',
              acc.migrate_wallet(p) == 3 and '137:210004:-1' not in p.d['resources']
              and sum(1 for a in p.accessories() if a['id'] == 210004) == 3)
        p.take_new_accessories()

        print('\nwearing them')
        unit, other = p.d['units'][0], p.d['units'][1]
        ear = next(a for a in p.accessories() if acc.item_type(a['id']) == 13)
        neck = next(a for a in p.accessories() if acc.item_type(a['id']) == 14)
        from hc.protocol.dto import TYPES

        def equip(u, slot, key):
            return call(s, 30007, vecChangeInfo=[TYPES['NGUnitEquipInfo'](
                UnitUID=u['uid'], ItemType=slot, ItemKey=key)])

        d = equip(unit, 13, ear['uid'])
        changed = d['_CheckInfo'].vecChangeAccessoryInfo or []
        check('an earring goes in slot 13',
              ear['equip'] == unit['uid'] and [c.EquipUnitUID for c in changed] == [unit['uid']])
        uinfo = state.unit_infos(p, [unit])[0]
        check('and rides on the hero (NGUnitInfo.vecAccessory)',
              [x.UID for x in uinfo.vecAccessory] == [ear['uid']])
        equip(unit, 13, neck['uid'])
        check('a necklace is refused in the earring slot', neck['equip'] == 0)
        equip(other, 13, ear['uid'])
        check('wearing it on another hero moves it',
              ear['equip'] == other['uid'] and '13' not in unit.get('equip', {}))
        equip(other, 13, 0)
        check('and it can come off', ear['equip'] == 0 and '13' not in other.get('equip', {}))
        check('an accessory nobody wears says EquipUnitUID -1 (the hero tab lists only those)',
              acc.info(ear).EquipUnitUID == -1)

        print('\nlocking and selling')
        call(s, 30207, uid=ear['uid'], LockEnable=1)
        gold = p.get_resource(0)
        d = call(s, 30209, _vecSell=[ear['uid']])
        check('a locked accessory is not sold', p.find_accessory(ear['uid']) is not None)
        call(s, 30207, uid=ear['uid'], LockEnable=0)
        d = call(s, 30209, _vecSell=[ear['uid']])
        check('unlocked, it sells for its SellGold and the client is told',
              p.find_accessory(ear['uid']) is None
              and p.get_resource(0) == gold + acc.sell_price(ear)
              and [x.UID for x in d['_CheckInfo'].vecDelAccessoryInfo] == [ear['uid']])

        print('\nlogin')
        equip(unit, 14, neck['uid'])
        s2 = FakeSession(p)
        asyncio.run(login_handler.login(s2, {
            'DeviceID': 'test-device-acc', 'AccountID': p.account_id, 'AuthType': 20,
            'MarketID': 0, 'ClientVersion': 'test', 'statDeviceID': 'x', 'DeviceType': 0,
            'UserLanguage': 0, 'CheckKT': 0, 'ResourceVersion': 0, 'IsReconnect': False,
            '_name': 'LogInReq'}))
        large = next(d for pid, d, *_ in s2.sent if pid == 40005)['ngAck']
        check('every accessory goes out at login',
              len(large.vecAccessoryInfo) == len(p.accessories()))
        worn = next(u for u in large.vecUnit if u.UID == unit['uid'])
        check('and the hero wearing one carries it',
              [x.UID for x in worn.vecAccessory] == [neck['uid']])
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
