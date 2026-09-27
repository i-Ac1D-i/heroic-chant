"""Dimension Crack and the daily ticket top-ups, through the handlers.

    python tools/test_training_tower.py

See hc/game/training_tower.py and hc/game/refills.py.
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
    sandbox = tempfile.mkdtemp(prefix='hc-tower-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    os.environ['HC_ACCOUNTS_DIR'] = os.path.join(sandbox, 'accounts')
    os.makedirs(os.environ['HC_ACCOUNTS_DIR'])

    from hc.game.player import Player
    from hc.game import refills, training_tower as tt
    from hc.game.enums import CollectionType
    from hc.protocol.dto import TYPES
    from hc.settings import SETTINGS
    from hc.net import HANDLERS
    import hc.handlers  # noqa: F401
    from hc.handlers import login as login_handler, not_open
    from tools.test_progression import FakeSession, call

    try:
        print('\ndaily top-ups')
        p = Player.create(900600, 'test-device-tt')
        p.d['resources'].pop('26:-1:-1', None)
        now = datetime(2026, 9, 27, 12)
        added = dict(((t1, t2), n) for t1, t2, n in refills.top_up(p, now))
        check('each tower gets its 3 Dimension Crack tickets',
              all(p.get_resource(28, g) == 3 for g in tt.GROUPS), added)
        check('an empty Hero Dungeon wallet comes back to 10', p.get_resource(26) == 10)
        check('a wallet above the cap is left alone (Arena starts at 99)',
              p.get_resource(30) == 99 and (30, -1) not in added)
        p.add_resource(28, -2, 1)
        refills.top_up(p, now + timedelta(hours=3))
        check('a second top-up the same day adds nothing', p.get_resource(28, 1) == 1)
        refills.top_up(p, now + timedelta(days=1))
        check('the next day refills it, to the cap and no further',
              p.get_resource(28, 1) == 3 and p.get_resource(28, 2) == 3)

        print('\nwhere the towers stand')
        check('StartReq is a real handler now, not the refusal',
              HANDLERS[30041].__module__.endswith('training_tower')
              and all(r[0] != 30041 for r in not_open.NOT_OPEN))
        check('a new account has no floor until the backfill...',
              p.get_resource(29, 1) == 0)
        tt.backfill(p)
        check('...which puts every tower on floor 1',
              all(p.get_resource(29, g) == 1 for g in tt.GROUPS))

        print('\na run')
        SETTINGS.set('tickets.daily_refill', False)     # tickets counted by hand now
        s = FakeSession(p)
        party = [TYPES['NGPartyInfo'](SlotType=151, SlotIndex=0,
                                      UnitUID=p.d['units'][0]['uid'], SkillOnOff=1)]
        d = call(s, 30041, groupID=1, vecPartyInfo=party, iSelectParty=0)
        check('start names tower 1 floor 1 and costs one of its tickets',
              d['Error'] == 0 and d['dungeonID'] == 20001 and p.get_resource(28, 1) == 2
              and p.get_resource(28, 2) == 3, (d, p.get_resource(28, 1)))
        check('and keeps the party', any(int(r['slot_type']) == 151 for r in p.parties()))
        gold = p.get_resource(2)
        d = call(s, 30042, groupID=1, dungeonID=20003, AuthEnable=False, iSelectParty=0,
                 _LogString='')
        check('clearing up to the floor-3 checkpoint pays floors 1..3',
              d['Error'] == 0 and all(p.get_collection(CollectionType.DungeonClearCount, t2=x) == 1
                                      for x in (20001, 20002, 20003))
              and p.get_resource(2) > gold, d['Error'])
        check('and saves: the tower is on floor 4, sent in the Ack',
              p.get_resource(29, 1) == 4
              and any(r.Type1 == 29 and r.Type2 == 1 and r.Value1 == 4
                      for r in d['_CheckInfo'].vecAddResourceInfo), d['_CheckInfo'].vecAddResourceInfo)
        d = call(s, 30042, groupID=1, dungeonID=20003, AuthEnable=False, iSelectParty=0,
                 _LogString='')
        check('the same End again pays nothing (the run is over)',
              p.get_collection(CollectionType.DungeonClearCount, t2=20003) == 1)

        print('\na lost run')
        call(s, 30041, groupID=1, vecPartyInfo=party, iSelectParty=0)
        d = call(s, 30042, groupID=1, dungeonID=20005, AuthEnable=False, iSelectParty=0,
                 _LogString='')
        check('lost on floor 6: floors 4 and 5 pay...',
              p.get_collection(CollectionType.DungeonClearCount, t2=20005) == 1
              and p.get_collection(CollectionType.DungeonClearCount, t2=20006) == 0)
        check('...but floor 6 is the save point, so the tower stays on floor 4',
              p.get_resource(29, 1) == 4, p.get_resource(29, 1))
        call(s, 30041, groupID=1, vecPartyInfo=party, iSelectParty=0)
        d = call(s, 30042, groupID=1, dungeonID=20003, AuthEnable=False, iSelectParty=0,
                 _LogString='')
        check('lost on the run\'s first floor (End names the floor before): nothing paid',
              d['Error'] == 0 and p.get_collection(CollectionType.DungeonClearCount, t2=20004) == 1
              and p.get_resource(29, 1) == 4)
        check('three runs, three tickets', p.get_resource(28, 1) == 0, p.get_resource(28, 1))
        d = call(s, 30041, groupID=1, vecPartyInfo=party, iSelectParty=0)
        check('without a ticket the start is refused', d['Error'] == 2, d['Error'])
        d = call(s, 30042, groupID=2, dungeonID=20105, AuthEnable=False, iSelectParty=0,
                 _LogString='')
        check('an End for a tower with no run pays nothing',
              p.get_collection(CollectionType.DungeonClearCount, t2=20101) == 0)

        print('\nthe top floor')
        p.add_resource(28, 1, 3)
        tt.set_current(p, 3, 100)
        d = call(s, 30041, groupID=3, vecPartyInfo=party, iSelectParty=0)
        check('floor 100 starts', d['Error'] == 0 and d['dungeonID'] == 20300, d)
        call(s, 30042, groupID=3, dungeonID=20300, AuthEnable=False, iSelectParty=0,
             _LogString='')
        check('clearing it keeps the tower on 100 (there is no 101)',
              p.get_resource(29, 3) == 100)

        print('\nskip')
        p.add_resource(28, 1, 2)
        before = p.get_resource(28, 2)
        d = call(s, 30043, vecGroupID=[2, 2, 1, 9])
        check('one ticket for tower 2; tower 1 has none; duplicates and junk ignored',
              d['Error'] == 0 and p.get_resource(28, 2) == before - 1
              and p.get_resource(28, 1) == 0, (d['Error'], p.get_resource(28, 2)))
        check('it pays the current floor\'s drops and does not move the tower',
              bool(d['_CheckInfo'].vecAddResourceInfo) and p.get_resource(29, 2) == 1)
        p.d['resources']['28:2:-1'] = 0
        p.d['resources']['28:3:-1'] = 0
        d = call(s, 30043, vecGroupID=[2, 3])
        check('with no tickets at all it is refused', d['Error'] == 2, d['Error'])

        print('\nlogin')
        SETTINGS.set('tickets.daily_refill', True)
        p2 = Player.create(900601, 'test-device-tt2')
        p2.save()
        s2 = FakeSession(p2)
        asyncio.run(login_handler.login(s2, {
            'DeviceID': 'test-device-tt2', 'AccountID': p2.account_id, 'AuthType': 20,
            'MarketID': 0, 'ClientVersion': 'test', 'statDeviceID': 'x', 'DeviceType': 0,
            'UserLanguage': 0, 'CheckKT': 0, 'ResourceVersion': 0, 'IsReconnect': False,
            '_name': 'LogInReq'}))
        large = next(d for pid, d, *_ in s2.sent if pid == 40005)
        sent = {(r.Type1, r.Type2): r.Value1
                for v in large.values() for r in getattr(v, 'vecResource', None) or []
                if r.Type1 in (28, 29)}
        check('a fresh login has every tower on floor 1 with 3 tickets',
              all(s2.player.get_resource(29, g) == 1 and s2.player.get_resource(28, g) == 3
                  for g in tt.GROUPS))
        check('and the client is told', all(sent.get((29, g)) == 1 and sent.get((28, g)) == 3
                                            for g in tt.GROUPS), sent)
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
