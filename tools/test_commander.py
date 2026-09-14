"""Commander Level Up and Command Center Level Up, driven through the real
handlers.

    python tools/test_commander.py

Covers CommanderLvUpReq (30030), CommandersLevelUpReq (30300, the packet the
screen actually sends), and CommandCenterLevelUpReq (30299, the building's
own Training button).
"""
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
    sandbox = tempfile.mkdtemp(prefix='hc-commander-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    accounts = os.path.join(sandbox, 'accounts')
    os.makedirs(accounts)

    from hc.game import player as player_mod
    player_mod.ACCOUNTS_DIR = accounts
    from hc.game.player import Player
    from hc.data.tables import TABLES, to_int
    import hc.handlers  # noqa: F401  (registers handlers)
    from tools.test_progression import FakeSession, call

    try:
        p = Player.create(900200, 'test-device-commander')
        s = FakeSession(p)
        c = p.find_commander(1)

        print('\nCommander Level Up')
        check('the starting commander exists', c is not None, c)
        check('CommandersID 1 is Catherine',
              TABLES.strings().get(to_int(TABLES.row(
                  'commandersStatInfo', 'CommandersID', 1).get('CommanderName')))
              == 'Catherine')

        d = call(s, 30030, commanderID=999)
        check('an unowned commander id is refused', d['Error'] != 0)

        row = next(r for r in TABLES.rows('commandersStatInfo', 'CommandersID', 1)
                  if to_int(r['Level']) == c['level'] + 1)
        rid, val = to_int(row['ResourceID_1']), to_int(row['Val_1'])
        p.d['resources'] = {}
        d = call(s, 30030, commanderID=1)
        check('not enough resources is refused', d['Error'] != 0, d['Error'])
        check('and the level did not move', c['level'] == 1)

        p.add_resource(rid, val, to_int(row.get('ResourceID_1_Type2'), -1))
        rid2 = to_int(row['ResourceID_2'])
        if rid2 >= 0:
            p.add_resource(rid2, to_int(row['Val_2']),
                           to_int(row.get('ResourceID_2_Type2'), -1))
        before = p.get_resource(rid, to_int(row.get('ResourceID_1_Type2'), -1))
        d = call(s, 30030, commanderID=1)
        check('levelling up with enough resources succeeds', d['Error'] == 0, d['Error'])
        check('the commander advanced a level', c['level'] == 2, c['level'])
        check('the change came back on the wire',
              d['_CheckInfo'].vecChangeCommander[0].Level == 2)
        check('the cost was actually spent',
              p.get_resource(rid, to_int(row.get('ResourceID_1_Type2'), -1)) < before)

        print('\nmax level')
        top = max(to_int(r['Level'])
                  for r in TABLES.rows('commandersStatInfo', 'CommandersID', 1))
        c['level'] = top
        d = call(s, 30030, commanderID=1)
        check('levelling past the table\'s top row is refused', d['Error'] != 0)

        print('\nCommanders Level Up (the packet the screen actually sends)')
        c0 = p.find_commander(0)
        check('Sarah Coldwell exists from account creation, not just Catherine',
              c0 is not None, c0)
        d = call(s, 30300, _CommanderID=999)
        check('a truly unowned commander id is refused', d['Error'] != 0)
        row0 = next(r for r in TABLES.rows('commandersStatInfo', 'CommandersID', 0)
                   if to_int(r['Level']) == c0['level'] + 1)
        for i in (1, 2):
            rid = to_int(row0.get('ResourceID_%d' % i), -1)
            if rid >= 0:
                p.add_resource(rid, to_int(row0['Val_%d' % i]),
                               to_int(row0.get('ResourceID_%d_Type2' % i), -1))
        d = call(s, 30300, _CommanderID=0)
        check('CommandersLevelUpReq succeeds', d['Error'] == 0, d['Error'])
        check('and carries the commander id back', d['_CommanderID'] == 0)
        check('Sarah Coldwell advanced a level', c0['level'] == 2, c0['level'])
        check('the change came back on the wire',
              d['_CheckInfo'].vecChangeCommander[0].Level == 2)
        added = d['_CheckInfo'].vecAddCommandersInfo
        check('vecAddCommandersInfo is what the button actually reads',
              len(added) == 1 and added[0].CommanderID == 0 and added[0].Level == 2,
              added)

        print('\nCommand Center Level Up')
        check('a fresh account has no building level yet',
              p.d.get('command_center_level', 0) == 0)
        row = TABLES.row('commandCenterStatInfo', 'Level', 1)
        d = call(s, 30299)
        check('not enough resources is refused', d['Error'] != 0, d['Error'])
        check('and the level did not move', p.d.get('command_center_level', 0) == 0)

        i = 1
        while ('ResourceID_%d' % i) in row:
            rid = to_int(row['ResourceID_%d' % i], -1)
            if rid >= 0:
                p.add_resource(rid, to_int(row['Val_%d' % i]),
                               to_int(row.get('ResourceID_%d_Type2' % i), -1))
            i += 1
        d = call(s, 30299)
        check('levelling up with enough resources succeeds', d['Error'] == 0, d['Error'])
        check('the building advanced a level',
              p.d.get('command_center_level') == 1, p.d.get('command_center_level'))
        check('the change came back on the wire',
              d['_CheckInfo'].vecAddCommandCenterInfo[0].Level == 1)

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
