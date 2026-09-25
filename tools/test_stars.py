"""Story stars, driven through the real handlers.

    python tools/test_stars.py

Stages keep a 3-bit star mask in collection 29 (Normal) / 30 (Hard) keyed by
dungeon id, each star pays its one-off reward the first time it is earned, and
the chests under a floor are claimed with GetDungeonStarRewardReq (30220).
The rules are read off the client; see hc/game/stars.py.
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
    sandbox = tempfile.mkdtemp(prefix='hc-stars-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    accounts = os.path.join(sandbox, 'accounts')
    os.makedirs(accounts)

    from hc.game import player as player_mod
    player_mod.ACCOUNTS_DIR = accounts
    from hc.game.player import Player
    from hc.game import stars
    from hc.game.enums import CollectionType
    from hc.data.tables import TABLES, to_int
    from hc.protocol.dto import encode_packet, decode_packet
    import hc.handlers  # noqa: F401
    from hc.handlers import login as login_handler
    from tools.test_progression import FakeSession, call

    def end(s, did, flag):
        return call(s, 30010, DungeonID=did, ClearType=0, AuthEnable=True,
                    _StarFlag=flag, _LogString='')

    def paid(d):
        return sorted((r.Type1, r.Type2, r.Value1) for r in (d['_vecStarReward'] or []))

    def expected(did, bits):
        return sorted((t1, t2, v) for t1, t2, _t3, v in stars.stage_rewards(did, bits))

    try:
        p = Player.create(900600, 'test-device-stars')
        s = FakeSession(p)
        stamina = 129
        p.add_resource(stamina, 10_000)

        print('\na stage\'s own stars')
        did = 1                                    # Season 0, Normal, floor 1
        d = end(s, did, 0b011)
        check('the clear is accepted', d['Error'] == 0, d['Error'])
        check('the mask lands in (29, dungeonID)',
              p.get_collection(CollectionType.NormalDungeonStarCount, t2=did) == 0b011,
              p.get_collection(CollectionType.NormalDungeonStarCount, t2=did))
        check('and reaches the client',
              any(c.Type1 == 29 and c.Type2 == did and c.Value1 == 3
                  for c in (d['_CheckInfo'].vecAddCollectionInfo or [])))
        check('stars 1 and 2 pay their rewards, not star 3',
              paid(d) == expected(did, 0b011) and paid(d), paid(d))

        d = end(s, did, 0b101)
        check('a later run adds the missing star', stars.stage_mask(p, 0, did) == 0b111)
        check('and pays only star 3', paid(d) == expected(did, 0b100), paid(d))
        d = end(s, did, 0b111)
        check('three stars again pays nothing', paid(d) == [], paid(d))
        d = end(s, did, 0b001)
        check('a worse run never takes stars away', stars.stage_mask(p, 0, did) == 0b111)

        print('\nthe floor\'s chests')
        floor_ids = stars.floor_dungeons(0, 0, 1)
        check('floor 1 of season 0 has ten stages', len(floor_ids) == 10, floor_ids)
        check('its star count is the sum of the masks', stars.floor_stars(p, 0, 0, 1) == 3)

        d = call(s, 30220, _ModeID=0, _StorySeason=0, _Floor=1, _StarCount=10)
        check('the 10-star chest is refused at 3 stars', d['Error'] != 0, d['Error'])
        for x in floor_ids[:4]:
            p.set_collection(CollectionType.NormalDungeonStarCount, 0b111, t2=x)
        check('four full stages make 12', stars.floor_stars(p, 0, 0, 1) == 12)
        rows = stars.chest_rows(0, 0, 1, 10)
        before = {(t1, t2): p.get_resource(t1, t2)
                  for t1, t2, _t3, _v in stars.chest_reward(rows)}
        d = call(s, 30220, _ModeID=0, _StorySeason=0, _Floor=1, _StarCount=10)
        check('now it opens', d['Error'] == 0, d['Error'])
        check('the Ack echoes what was asked', (d['_ModeID'], d['_StorySeason'],
              d['_Floor'], d['_StarCount']) == (0, 0, 1, 10))
        check('the chest pays', all(p.get_resource(t1, t2) == before[(t1, t2)] + v
                                    for t1, t2, _t3, v in stars.chest_reward(rows)))
        check('the chest is marked with season 0\'s flag in (31, floor, goal)',
              p.get_collection(CollectionType.NormalDungeonFloorReward, t2=1, t3=10) == 1)
        check('and the client is told',
              any(c.Type1 == 31 and c.Type2 == 1 and c.Type3 == 10 and c.Value1 == 1
                  for c in (d['_CheckInfo'].vecAddCollectionInfo or [])))
        d = call(s, 30220, _ModeID=0, _StorySeason=0, _Floor=1, _StarCount=10)
        check('it cannot be opened twice', d['Error'] != 0)
        d = call(s, 30220, _ModeID=0, _StorySeason=0, _Floor=1, _StarCount=20)
        check('the 20-star chest still waits', d['Error'] != 0)
        d = call(s, 30220, _ModeID=0, _StorySeason=0, _Floor=1, _StarCount=11)
        check('a chest that does not exist is refused', d['Error'] != 0)

        # Season 1's floors have 25 stages and chests at 25/50/75.  The claim
        # record is keyed by (floor, goal) only, so seasons share the key and
        # are told apart by the flag: season 0 is 1, season 1 is 2.
        s1 = stars.floor_dungeons(0, 1, 1)
        for x in s1[:9]:
            p.set_collection(CollectionType.NormalDungeonStarCount, 0b111, t2=x)
        d = call(s, 30220, _ModeID=0, _StorySeason=1, _Floor=1, _StarCount=25)
        check('season 1\'s 25-star chest opens with its own flag',
              d['Error'] == 0 and p.get_collection(
                  CollectionType.NormalDungeonFloorReward, t2=1, t3=25) == 2,
              (d['Error'], p.get_collection(CollectionType.NormalDungeonFloorReward,
                                            t2=1, t3=25)))

        print('\nHard stages use their own collections')
        hard = stars.floor_dungeons(13, 0, 1)
        check('Hard floor 1 exists', len(hard) > 0)
        stars.record_stage(p, TABLES.dungeon(hard[0]), 0b111)
        check('a Hard mask goes to collection 30',
              p.get_collection(CollectionType.HardDungeonStarCount, t2=hard[0]) == 7
              and p.get_collection(CollectionType.NormalDungeonStarCount, t2=hard[0]) == 0)

        print('\nold saves are moved over once')
        old = Player.create(900601, 'test-device-stars-old')
        old.d['cleared'] = {'1': 7, '2': 3, '15': 1}
        old.d['collections'] = {
            '0:1:-1': 7, '0:2:-1': 3, '0:15:-1': 1,
            # what the old code wrote: star counts keyed by *floor*
            '29:1:-1': 11, '29:2:-1': 6,
        }
        check('the migration runs', stars.migrate(old))
        check('stage 1 keeps its mask', stars.stage_mask(old, 0, 1) == 7)
        check('stage 2 is its mask, not floor 2\'s count', stars.stage_mask(old, 0, 2) == 3)
        check('stage 15 keeps its one star', stars.stage_mask(old, 0, 15) == 1)
        check('no other 29 keys are left',
              sorted(k for k in old.d['collections'] if k.startswith('29:'))
              == ['29:15:-1', '29:1:-1', '29:2:-1'], sorted(old.d['collections']))
        check('it does not run twice', not stars.migrate(old))
        s_old = FakeSession(old)
        old.add_resource(stamina, 1000)
        d = end(s_old, 2, 0b111)
        check('star rewards the old code already paid are not paid again',
              paid(d) == [], paid(d))
        check('but the new star still shows', stars.stage_mask(old, 0, 2) == 7)

        ack = decode_packet(40005, encode_packet(40005, login_handler._large_data(old)))
        sent = {(c.Type1, c.Type2): c.Value1 for c in ack['ngAck'].vecCollectionInfo}
        check('the masks go out at login', sent.get((29, 1)) == 7 and sent.get((29, 2)) == 7,
              {k: v for k, v in sent.items() if k[0] == 29})
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
