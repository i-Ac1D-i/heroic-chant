"""Achievement rewards, driven through the real handler.

    python tools/test_achievements.py

GetAchievementRewardReq (30129) had no handler -- claiming did nothing.
Covers the two ClearTypes reachable on this server: resource-balance based
(achievementID 1) and GachaOpenCount based (achievementID 1001).
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
    sandbox = tempfile.mkdtemp(prefix='hc-achievements-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    accounts = os.path.join(sandbox, 'accounts')
    os.makedirs(accounts)

    from hc.game import player as player_mod
    player_mod.ACCOUNTS_DIR = accounts
    from hc.game.player import Player
    from hc.game import achievements
    from hc.game.enums import CollectionType
    import hc.handlers  # noqa: F401
    from tools.test_progression import FakeSession, call

    try:
        p = Player.create(900300, 'test-device-achievements')
        s = FakeSession(p)

        print('\nResource-based achievement (Hero Point, ID 1)')
        d = call(s, 30129, _vecAchievementID=[1])
        check('not yet complete is refused', d['Error'] != 0, d['Error'])
        check('nothing claimed', 1 not in achievements.claimed(p))

        p.add_resource(110, 60)
        d = call(s, 30129, _vecAchievementID=[1])
        check('claiming once the threshold is met succeeds', d['Error'] == 0, d['Error'])
        check('the id came back', d['_vecAchievementID'] == [1])
        check('and is now recorded as claimed', 1 in achievements.claimed(p))
        gold = p.get_resource(0)
        d = call(s, 30129, _vecAchievementID=[1])
        check('claiming the same id twice is refused', d['Error'] != 0)
        check('and pays nothing the second time', p.get_resource(0) == gold)

        print('\nCollection-based achievement (GachaOpenCount, ID 1001)')
        d = call(s, 30129, _vecAchievementID=[1001])
        check('not yet complete is refused', d['Error'] != 0, d['Error'])

        p.add_collection(CollectionType.GachaOpenCount, 2, t2=1)
        d = call(s, 30129, _vecAchievementID=[1001])
        check('claiming once the threshold is met succeeds', d['Error'] == 0, d['Error'])
        check('and is now recorded as claimed', 1001 in achievements.claimed(p))

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
