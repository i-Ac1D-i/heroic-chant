"""The Selective Cube (GachaID 1000), driven through the real handlers.

    python tools/test_select_gacha.py

Reroll as often as you like, claim once: see hc/game/select_gacha.py.
"""
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
    sandbox = tempfile.mkdtemp(prefix='hc-selectgacha-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    os.environ['HC_ACCOUNTS_DIR'] = os.path.join(sandbox, 'accounts')
    os.makedirs(os.environ['HC_ACCOUNTS_DIR'])

    from hc.game.player import Player
    from hc.game import select_gacha
    from hc.data.tables import TABLES
    from hc.settings import SETTINGS
    import hc.handlers  # noqa: F401
    from hc.handlers import login as login_handler
    from tools.test_progression import FakeSession, call

    try:
        print('\nwhat a roll looks like')
        rng = random.Random(7)
        rare_of = {}
        for tier in TABLES.rows('unitGachaID', 'GachaID', 1000, source='json'):
            for p in TABLES.rows('unitRateGroup', 'GroupID', int(tier['GroupID']), source='json'):
                rare_of.setdefault(int(p['UnitID']), set()).add(int(tier['Rareness']))
        shapes, bad = set(), []
        for _ in range(400):
            got = select_gacha.roll(1000, rng)
            counts = [sum(1 for _u, r in got if r == k) for k in (2, 1, 0)]
            shapes.add(tuple(counts))
            if len(got) != 10 or counts[0] != 1 or not 1 <= counts[1] <= 3:
                bad.append(counts)
            if any(r not in rare_of.get(u, ()) for u, r in got):
                bad.append(('pool', got))
        check('400 rolls: always ten, exactly one SS, one to three S', not bad, bad[:3])
        check('and the S count actually varies', len({s[1] for s in shapes}) >= 2, shapes)

        print('\nthe packets')
        p = Player.create(900800, 'test-device-select')
        s = FakeSession(p)
        p.d['cleared'] = {}
        d = call(s, 30269, GachaID=1000)
        check('before stage 5 it is not open (-901)', d['Error'] == -901, d['Error'])
        d = call(s, 30269, GachaID=4242)
        check('an unknown cube is "information missing" (-900)', d['Error'] == -900, d['Error'])
        d = call(s, 30270, GachaID=1000)
        check('claiming while still locked is refused', d['Error'] != 0)

        for did in range(1, 6):
            p.mark_cleared(did, 7)
        d = call(s, 30270, GachaID=1000)
        check('claiming with nothing rolled is -903', d['Error'] == -903, d['Error'])

        units_before = len(p.d['units'])
        d = call(s, 30269, GachaID=1000)
        first = [(r.Type1, r.Type2) for r in d['vecSelectReward']]
        check('a roll comes back as ten heroes (ResourceType 1 / unit id)',
              d['Error'] == 0 and len(first) == 10 and all(t == 1 for t, _u in first), d)
        d = call(s, 30269, GachaID=1000)
        second = [r.Type2 for r in d['vecSelectReward']]
        check('rerolling works and grants nothing',
              d['Error'] == 0 and len(p.d['units']) == units_before)
        check('the claim is for the last roll',
              [u for u, _r in select_gacha.pending(p, 1000)] == second)

        owned = {int(u['id']) for u in p.d['units']}
        expect_new = len({u for u in second if u not in owned})
        d = call(s, 30270, GachaID=1000)
        new_units = d['_CheckInfo'].vecAddUnitInfo or []
        check('claiming grants the heroes you don\'t have yet',
              d['Error'] == 0 and len(p.d['units']) == units_before + expect_new
              and len(new_units) == expect_new, (len(p.d['units']), units_before, expect_new))
        check('and says so with vecAddUnitInfo', all(u.UnitID in second for u in new_units))
        check('the claim comes back as NGSelectGacha',
              [(g.GachaID, bool(g.RecvTime)) for g in d['vecSelectGacha']] == [(1000, True)],
              d['vecSelectGacha'])
        d = call(s, 30270, GachaID=1000)
        check('a second claim is refused (-902)', d['Error'] == -902, d['Error'])
        d = call(s, 30269, GachaID=1000)
        check('and so is another roll', d['Error'] == -902, d['Error'])
        check('the summon missions did not count it',
              not any('summon' in k for k in p.d.get('missions', {}).get('counters', {})))

        print('\nlogin')
        s2 = FakeSession(p)
        import asyncio
        asyncio.run(login_handler.login(s2, {
            'DeviceID': 'test-device-select', 'AccountID': p.account_id, 'AuthType': 20,
            'MarketID': 0, 'ClientVersion': 'test', 'statDeviceID': 'x', 'DeviceType': 0,
            'UserLanguage': 0, 'CheckKT': 0, 'ResourceVersion': 0, 'IsReconnect': False,
            '_name': 'LogInReq'}))
        ack03 = next(d for pid, d, *_ in s2.sent if pid == 40002)['ngAck']
        check('login tells the client the cube is used',
              [g.GachaID for g in ack03.vecSelectGacha] == [1000], ack03.vecSelectGacha)
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
