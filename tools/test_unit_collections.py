"""Hero Collection unlock, driven through the real handler.

    python tools/test_unit_collections.py

UnlockUnitCollectionReq (30334) had no handler -- claiming a set's stat bonus
did nothing, and nothing was ever sent at login, so the collection screen
showed every set as unclaimed regardless of grade.
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
    sandbox = tempfile.mkdtemp(prefix='hc-unitcollection-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    accounts = os.path.join(sandbox, 'accounts')
    os.makedirs(accounts)

    from hc.game import player as player_mod
    player_mod.ACCOUNTS_DIR = accounts
    from hc.game.player import Player
    from hc.game import unit_collections as uc
    import hc.handlers  # noqa: F401
    from tools.test_progression import FakeSession, call

    try:
        p = Player.create(900400, 'test-device-unitcollection')
        s = FakeSession(p)

        def set_grade(unit_id, grade):
            u = next(u for u in p.d['units'] if int(u['id']) == unit_id)
            u['grade'] = grade

        set_id = 1001
        for uid in (1, 10, 35):
            set_grade(uid, 0)

        print('\nHero Collection unlock (set %d, heroes 1/10/35)' % set_id)
        check('nothing achievable at grade 0', uc.achievable_step(p, set_id) == 0)
        d = call(s, 30334, _id=set_id)
        check('claiming with nothing achievable is refused', d['_iError'] != 0, d['_iError'])

        for uid in (1, 10, 35):
            set_grade(uid, 1)
        check('step 1 (sum 3) is now achievable', uc.achievable_step(p, set_id) == 1)
        d = call(s, 30334, _id=set_id)
        check('claiming succeeds', d['_iError'] == 0, d['_iError'])
        check('and is recorded', uc.claimed_step(p, set_id) == 1)
        changed = d['_checkInfo'].vecChangeUnitCollection
        check('the change came back on the wire',
              len(changed) == 1 and changed[0].ID == set_id and changed[0].Step == 1,
              changed)

        d = call(s, 30334, _id=set_id)
        check('claiming the same step again is refused', d['_iError'] != 0)

        for uid in (1, 10, 35):
            set_grade(uid, 2)
        check('step 2 (sum 6) is now achievable', uc.achievable_step(p, set_id) == 2)
        d = call(s, 30334, _id=set_id)
        check('claiming the next step succeeds', d['_iError'] == 0, d['_iError'])
        check('and advances the recorded step', uc.claimed_step(p, set_id) == 2)

        print('\nMissing a required hero')
        other_set = next(sid for sid in uc.all_ids() if sid != set_id)
        rows = uc.steps_for(other_set)
        missing_hero = int(rows[0]['HeroID1'])
        p.d['units'] = [u for u in p.d['units'] if int(u['id']) != missing_hero]
        check('a set missing one of its heroes is never achievable',
              uc.achievable_step(p, other_set) == 0)

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
