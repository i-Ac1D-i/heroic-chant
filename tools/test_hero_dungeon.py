"""Hero Dungeon and the calendar seasons, through the handlers.

    python tools/test_hero_dungeon.py

See hc/game/hero_dungeon.py and state.season_values.
"""
import asyncio
import os
import shutil
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s  %s%s' % ('PASS' if cond else 'FAIL', name,
                          '' if cond else ('  -- ' + str(detail))))


def main():
    sandbox = tempfile.mkdtemp(prefix='hc-herodungeon-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    os.environ['HC_ACCOUNTS_DIR'] = os.path.join(sandbox, 'accounts')
    os.makedirs(os.environ['HC_ACCOUNTS_DIR'])

    from hc.game.player import Player
    from hc.game import state, hero_dungeon as hd, missions
    from hc.game.enums import CollectionType
    from hc.protocol.dto import TYPES
    from hc.settings import SETTINGS
    import hc.handlers  # noqa: F401
    from hc.handlers import login as login_handler
    from tools.test_progression import FakeSession, call

    try:
        print('\ncalendar seasons')
        now = datetime(2026, 9, 27, 15)
        vals = {v.iSeasonType: v for v in state.season_values()}
        daily = state.season_info(state.SEASON_DAILY, now)
        check('the Daily value is the date, running midnight to midnight',
              daily.iSeasonValue == now.date().toordinal()
              and daily.tmStartDate == datetime(2026, 9, 27)
              and daily.tmEndDate == datetime(2026, 9, 28))
        check('and it matches the daily missions\' period',
              state.season_value(state.SEASON_DAILY, now) == missions.period(1, now)
              and state.season_value(state.SEASON_WEEKLY, now) == missions.period(2, now))
        check('the arena season is untouched', vals[state.SEASON_ARENA].iSeasonValue == 1)
        SETTINGS.set('seasons.calendar', False)
        check('seasons.calendar false goes back to the fixed value',
              state.season_value(state.SEASON_DAILY, now) == 1)
        SETTINGS.set('seasons.calendar', True)

        print('\nstarting a stage')
        p = Player.create(900500, 'test-device-hd')
        s = FakeSession(p)
        p.d['resources'].pop('26:-1:-1', None)
        party = [TYPES['NGPartyInfo'](SlotType=301, SlotIndex=0,
                                      UnitUID=p.d['units'][0]['uid'], SkillOnOff=1)]
        d = call(s, 30037, dungeonID=10001, vecPartyInfo=party)
        check('without a ticket it is refused', d['Error'] == 2, d['Error'])
        p.add_resource(26, 5)
        d = call(s, 30037, dungeonID=10002, vecPartyInfo=party)
        check('stage 2 before stage 1 is locked', d['Error'] == 4, d['Error'])
        d = call(s, 30037, dungeonID=99999, vecPartyInfo=party)
        check('an unknown stage is refused', d['Error'] != 0)
        d = call(s, 30037, dungeonID=10001, vecPartyInfo=party)
        check('with a ticket it starts, costs one, and keeps the party',
              d['Error'] == 0 and p.get_resource(26) == 4
              and any(int(r['slot_type']) == 301 for r in p.parties()), d['Error'])

        print('\nclearing it')
        gold = p.get_resource(0)
        d = call(s, 30038, dungeonIDVec=[10001], ClearType=0, AuthEnable=False, _LogString='')
        info = d['clearInfoVec']
        check('the clear comes back as NGHeroDungeonClearInfo with today\'s Daily value',
              d['Error'] == 0 and [(i.DungeonID, i.ClearDailySeason) for i in info]
              == [(10001, hd.today())], info)
        check('HeroDungeonClearCount counts it (the client reads it per stage)',
              p.get_collection(CollectionType.HeroDungeonClearCount, t2=10001) == 1)
        check('and the stage\'s first-clear drops are paid',
              bool(d['_CheckInfo'].vecAddResourceInfo), d['_CheckInfo'].vecAddResourceInfo)
        d = call(s, 30037, dungeonID=10001, vecPartyInfo=party)
        check('the same stage again today is refused, ticket kept',
              d['Error'] == 3 and p.get_resource(26) == 4, (d['Error'], p.get_resource(26)))
        d = call(s, 30037, dungeonID=10002, vecPartyInfo=party)
        check('stage 2 is open now', d['Error'] == 0)
        call(s, 30038, dungeonIDVec=[10002], ClearType=1, AuthEnable=False, _LogString='')

        print('\nskipping')
        d = call(s, 30038, dungeonIDVec=[10001, 10002], ClearType=3, AuthEnable=False, _LogString='')
        check('stages cleared today cannot be skipped', d['Error'] != 0 and p.get_resource(26) == 3)
        yesterday = hd.today() - 1
        p.d['hero_dungeon'] = {'10001': yesterday, '10002': yesterday}
        d = call(s, 30038, dungeonIDVec=[10001, 10002, 10003], ClearType=3, AuthEnable=False,
                 _LogString='')
        check('a day later both skip for a ticket each; a never-cleared one does not',
              sorted(i.DungeonID for i in d['clearInfoVec']) == [10001, 10002]
              and p.get_resource(26) == 1, (d['clearInfoVec'], p.get_resource(26)))
        check('a repeat pays the repeat drops, not the first-clear ones',
              p.get_collection(CollectionType.HeroDungeonClearCount, t2=10001) == 2)

        print('\nlogin')
        s2 = FakeSession(p)
        asyncio.run(login_handler.login(s2, {
            'DeviceID': 'test-device-hd', 'AccountID': p.account_id, 'AuthType': 20,
            'MarketID': 0, 'ClientVersion': 'test', 'statDeviceID': 'x', 'DeviceType': 0,
            'UserLanguage': 0, 'CheckKT': 0, 'ResourceVersion': 0, 'IsReconnect': False,
            '_name': 'LogInReq'}))
        ack01 = next(d for pid, d, *_ in s2.sent if pid == 40000)['ngAck']
        check('the clears go out at login',
              sorted(i.DungeonID for i in ack01.vecHeroDungeonClearInfo) == [10001, 10002])
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
