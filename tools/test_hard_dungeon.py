"""Hard story stages, through the handlers.

    python tools/test_hard_dungeon.py

See the Hard section of hc/handlers/dungeon.py.
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
    sandbox = tempfile.mkdtemp(prefix='hc-hard-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    os.environ['HC_ACCOUNTS_DIR'] = os.path.join(sandbox, 'accounts')
    os.makedirs(os.environ['HC_ACCOUNTS_DIR'])

    from hc.game.player import Player
    from hc.game.enums import CollectionType
    from hc.protocol.dto import TYPES
    from hc.net import HANDLERS
    import hc.handlers  # noqa: F401
    from hc.handlers import dungeon, not_open
    from tools.test_progression import FakeSession, call

    try:
        p = Player.create(901000, 'test-device-hard')
        s = FakeSession(p)
        party = [TYPES['NGPartyInfo'](SlotType=13, SlotIndex=0,
                                      UnitUID=p.d['units'][0]['uid'], SkillOnOff=1)]

        def start(did):
            return call(s, 30158, dungeonID=did, vecPartyInfo=party)

        def end(did, won=True, star=7):
            return call(s, 30159, DungeonID=did, ClearType=1 if won else 2,
                        AuthEnable=False, _StarFlag=star, _LogString='')

        print('\nunlocks')
        check('HardDungeonStartReq is handled, not refused',
              HANDLERS[30158].__module__.endswith('dungeon')
              and all(r[0] != 30158 for r in not_open.NOT_OPEN))
        opened = {o.DungeonID for o in dungeon.open_enables(p)}
        check('Hard 5001 is not open before Normal 140', 5001 not in opened)
        p.mark_cleared(140)
        opened = {o.DungeonID for o in dungeon.open_enables(p)}
        check('clearing Normal 140 opens Hard 5001 but not 5002',
              5001 in opened and 5002 not in opened)

        print('\na win')
        p.d['resources']['123:-1:-1'] = 2
        d = start(5001)
        check('it starts with a Hard ticket in hand, ticket not spent yet',
              d['Error'] == 0 and d['dungeonID'] == 5001 and p.get_resource(123) == 2, d['Error'])
        d = end(5001, star=5)
        check('a win uses one ticket', d['Error'] == 0 and p.get_resource(123) == 1)
        check('and records the stars (HardDungeonStarCount) and the clear',
              p.get_collection(CollectionType.HardDungeonStarCount, t2=5001) == 5
              and p.is_cleared(5001))
        check('its first-clear drops are paid',
              any(r.Type1 == 127 for r in d['_CheckInfo'].vecAddResourceInfo))
        check('and Hard 5002 opens', [o.DungeonID for o in d['_vecDungeonOpenEnable']] == [5002],
              d['_vecDungeonOpenEnable'])

        print('\na loss')
        stamina = p.get_resource(129)
        start(5002)
        d = end(5002, won=False)
        check('costs the 15-stamina FailCost, not the ticket',
              p.get_resource(129) == stamina - 15 and p.get_resource(123) == 1
              and not p.is_cleared(5002))
        d = end(5002)
        check('an End with no battle started pays nothing', not p.is_cleared(5002))

        print('\nno ticket')
        p.d['resources']['123:-1:-1'] = 0
        d = start(5002)
        check('is -468, which the client answers with its buy-tickets popup',
              d['Error'] == -468, d['Error'])
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
