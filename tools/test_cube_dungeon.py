"""The Cube Dungeon, through the handlers.

    python tools/test_cube_dungeon.py

See hc/game/cube_dungeon.py.
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
    sandbox = tempfile.mkdtemp(prefix='hc-cube-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    os.environ['HC_ACCOUNTS_DIR'] = os.path.join(sandbox, 'accounts')
    os.makedirs(os.environ['HC_ACCOUNTS_DIR'])

    from hc.game.player import Player
    from hc.game import cube_dungeon as cube, events
    from hc.net import HANDLERS
    from hc.settings import SETTINGS
    import hc.handlers  # noqa: F401
    from hc.handlers import login as login_handler, not_open
    from tools.test_progression import FakeSession, call

    try:
        print('\nthe season')
        check('seasons 2..25 in the table', cube.seasons() == list(range(2, 26)))
        a = cube.current_season(datetime(2026, 9, 27))[0]
        b = cube.current_season(datetime(2026, 10, 2))[0]
        check('it rotates a calendar month at a time', a != b and a in cube.seasons(), (a, b))
        SETTINGS.set('cube_dungeon.season', 7)
        check('cube_dungeon.season pins one', cube.current_season()[0] == 7)
        ev = [e for e in events.check_event_info().vecEventInfo if e.ID == 5037]
        check('the type-5037 event goes out with the season in Arg1',
              len(ev) == 1 and ev[0].Arg1 == '7' and ev[0].tmEnd > datetime.utcnow(), ev)
        check('StartCubeDungeonReq is handled, not refused',
              HANDLERS[30335].__module__.endswith('cube_dungeon')
              and all(r[0] != 30335 for r in not_open.NOT_OPEN))

        print('\nclimbing (season 3, paid in stamina)')
        SETTINGS.set('cube_dungeon.season', 3)
        p = Player.create(900900, 'test-device-cube')
        s = FakeSession(p)
        f1, f2 = cube.floor(3, 1), cube.floor(3, 2)
        d1, d2 = int(f1['DungeonID']), int(f2['DungeonID'])

        def end(number, did, won=True):
            return call(s, 30336, _Type=0, _Floor=number, _DungeonID=did,
                        _ClearType=1 if won else 2, _TotalPower=1, _TurnCnt=1,
                        _LogString='', _unitSettingSnapshot='')

        d = call(s, 30335, _Floor=2, _DungeonID=d2)
        check('floor 2 first is refused', d['_iError'] != 0)
        d = call(s, 30335, _Floor=1, _DungeonID=d2)
        check('a floor with the wrong stage is refused', d['_iError'] != 0)
        stamina = p.get_resource(129)
        d = call(s, 30335, _Floor=1, _DungeonID=d1)
        check('floor 1 starts and charges its stamina',
              d['_iError'] == 0 and p.get_resource(129) == stamina - int(f1['CostValue']))
        d = end(1, d1, won=False)
        check('a loss clears nothing', d['_userCubeDungeonInfo'].ClearFloor == 0)
        call(s, 30335, _Floor=1, _DungeonID=d1)
        d = end(1, d1)
        i = d['_userCubeDungeonInfo']
        check('a win: ClearFloor 1, Received 0, this season',
              (i.Season, i.ClearFloor, i.Received) == (3, 1, 0), i)
        d = call(s, 30335, _Floor=2, _DungeonID=d2)
        check('floor 2 waits for floor 1\'s reward', d['_iError'] != 0)
        d = call(s, 30337, _Floor=1)
        got = {(r.Type1, r.Type2) for r in d['_checkInfo'].vecAddResourceInfo}
        want = {(int(f1['Reward1_Type1']), int(f1['Reward1_Type2'])),
                (int(f1['Reward2_Type1']), int(f1['Reward2_Type2']))}
        check('taking it pays both floor rewards and sets Received',
              d['_iError'] == 0 and want <= got and d['_userCubeDungeonInfo'].Received == 1,
              (got, want))
        d = call(s, 30337, _Floor=1)
        check('only once', d['_iError'] != 0)
        d = call(s, 30335, _Floor=2, _DungeonID=d2)
        check('now floor 2 opens', d['_iError'] == 0)

        print('\na new season (8, paid in Cube coins)')
        SETTINGS.set('cube_dungeon.season', 8)
        i = cube.info(p)
        check('starts the climb over', (i.Season, i.ClearFloor, i.Received) == (8, 0, 0), i)
        from hc.game import refills
        refills.top_up(p)
        check('the daily top-up hands out 10 Cube coins (EventCoin 86)',
              p.get_resource(119, 86) == 10)
        g1 = cube.floor(8, 1)
        d = call(s, 30335, _Floor=1, _DungeonID=int(g1['DungeonID']))
        check('and a floor costs one', d['_iError'] == 0 and p.get_resource(119, 86) == 9,
              d['_iError'])

        print('\nlogin')
        p.save()
        s2 = FakeSession(p)
        asyncio.run(login_handler.login(s2, {
            'DeviceID': 'test-device-cube', 'AccountID': p.account_id, 'AuthType': 20,
            'MarketID': 0, 'ClientVersion': 'test', 'statDeviceID': 'x', 'DeviceType': 0,
            'UserLanguage': 0, 'CheckKT': 0, 'ResourceVersion': 0, 'IsReconnect': False,
            '_name': 'LogInReq'}))
        ack02 = next(d for pid, d, *_ in s2.sent if pid == 40001)['ngAck']
        ack03 = next(d for pid, d, *_ in s2.sent if pid == 40002)['ngAck']
        check('userCubeDungeonInfo and the season event go out at login',
              ack02.userCubeDungeonInfo.Season == 8
              and any(e.ID == 5037 and e.Arg1 == '8' for e in ack03.EventInfo.vecEventInfo))
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
