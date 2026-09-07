"""End-to-end test for the Arena.

    python tools/test_arena.py

Drives the real packet handlers against a throwaway accounts directory, with a
fake session that encodes every reply exactly as the wire does -- so a DTO that
would throw inside the client's marshaller fails here instead.

Covers the two cases that matter and are easy to get wrong:

  * one account on the server, where every opponent has to be a bot
  * two accounts, where they must be able to find and fight each other

Touches nothing in server/accounts or server/settings.json.
"""
import asyncio
import json
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


class FakeSession(object):
    """Enough of hc.net.Session for a handler, and it really encodes."""

    def __init__(self, player):
        self.player = player
        self.account = player.account_id
        self.sent = []

    async def send(self, packet_id, *args):
        from hc.protocol.dto import encode_packet, decode_packet
        body = encode_packet(packet_id, *args)       # would raise on a bad DTO
        decoded = decode_packet(packet_id, body)
        assert not decoded.get('_trailing'), (
            'packet %d left %d trailing bytes' % (packet_id, decoded['_trailing']))
        self.sent.append((packet_id, decoded, len(body)))
        return decoded

    def last(self, packet_id=None):
        for pid, decoded, _ in reversed(self.sent):
            if packet_id is None or pid == packet_id:
                return decoded
        return None


def call(session, packet_id, **args):
    from hc.net import HANDLERS
    args.setdefault('_name', 'test')
    asyncio.run(HANDLERS[packet_id](session, args))
    return session.last()


def party(units):
    from hc.protocol.dto import TYPES
    return [TYPES['NGPartyInfo'](SlotType=0, SlotIndex=i, UnitUID=int(u['uid']),
                                 SkillOnOff=1)
            for i, u in enumerate(units)]


def main():
    sandbox = tempfile.mkdtemp(prefix='hc-arena-')
    accounts = os.path.join(sandbox, 'accounts')
    os.makedirs(accounts)
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')

    from hc.game import player as player_mod
    player_mod.ACCOUNTS_DIR = accounts
    from hc.settings import SETTINGS
    from hc.game import arena
    from hc.game.player import Player
    import hc.handlers                                    # registers everything

    print('sandbox:', sandbox)
    SETTINGS.set('account.grant_all_heroes', False)
    SETTINGS.load()

    try:
        print('\ntables')
        check('32 tiers load', len(arena.tiers()) == 32, len(arena.tiers()))
        check('tier 1 is Bronze', arena.tier_name(0) == 'Bronze', arena.tier_name(0))
        check('a rating picks the right tier',
              arena.tier_number(0) == 1 and arena.tier_number(300) == 2
              and arena.tier_number(1500) == 6,
              [arena.tier_number(x) for x in (0, 300, 1500)])
        check('the shipped AI teams load', len(arena.bots()) > 800, len(arena.bots()))
        check('every bot has a team',
              all(b['units'] for b in arena.bots()[:50]))
        check('a win against a bot gains points and a loss costs fewer',
              arena.point_delta(0, True, 0, True) > 0
              > arena.point_delta(0, False, 0, True),
              (arena.point_delta(0, True, 0, True), arena.point_delta(0, False, 0, True)))
        check('beating someone above you is worth more than someone below',
              arena.point_delta(1000, True, 2000, False)
              > arena.point_delta(1000, True, 10, False))

        print('\nclient preconditions')
        from hc.game import state
        import datetime as _dt
        types = [v.iSeasonType for v in state.season_values()]
        check('an arena season is advertised', state.SEASON_ARENA in types, types)
        check('every NGCheckServerInfo carries it',
              any(v.iSeasonType == state.SEASON_ARENA
                  for v in state.check_info().vecSeasonValue))
        # NMUserInfo.CheckFindNewArenaMatchUserReq @0x13B007C bails when
        # GetNGSeasonValue(SeasonType.Arena) comes back null, and
        # ArenaMatchPlayer.StartMatch @0x1FAF560 sends nothing unless that
        # check is clean -- the Fight button just does nothing.
        sv = [v for v in state.season_values()
              if v.iSeasonType == state.SEASON_ARENA][0]
        check('the arena season window is open right now',
              sv.tmStartDate < _dt.datetime.utcnow() < sv.tmEndDate)

        print('\none player, bots only')
        alice = Player.create(3001, 'dev-alice')
        for uid in (1, 2, 3, 4):
            alice.add_unit(uid, level=50)
        alice.save()
        s = FakeSession(alice)

        ack = call(s, 30053)                                  # CheckArenaInfoReq
        check('opening the arena works', ack['Error'] == 0, ack.get('Error'))
        check('five opponents are offered', len(ack['vecNewMatchUser']) == 5,
              len(ack['vecNewMatchUser']))
        check('all of them are bots (nobody else is on the server)',
              all(m.ngArenaInfo.iAccountID < 0 for m in ack['vecNewMatchUser']))
        check('each opponent has a visible team',
              all(m.vecUnitInfo for m in ack['vecNewMatchUser']))
        check('the standing is reported', ack['ngArenaInfo'].strNickname == alice.nickname)

        call(s, 30054, vecChangePartyInfo=party(alice.d['units'][:4]))
        check('a defence team is stored',
              len(arena.record(Player.load(3001))['defense']) == 4)

        target = arena.record(alice)['matches'][0]
        before = arena.record(alice)['points']
        call(s, 30057, vecPartyInfo=party(alice.d['units'][:4]),
             _iMatchUserAccountID=target['account_id'], _iMatchIndex=target['slot'],
             dungeonID=0, randomSeed=1)
        check('joining a fight is accepted', s.last()['iError'] == 0, s.last().get('iError'))

        ack = call(s, 30058, iWinLose=1, iMyTotalPower=4000,
                   iMatchTotalPower=target['power'],
                   _iMatchUserAccount=target['account_id'],
                   _iMatchIndex=target['slot'], dungeonID=0,
                   strRecordInfo='', AuthEnable=False, _LogString='')
        after = arena.record(Player.load(3001))['points']
        check('a win raises the rating', after > before, (before, after))
        check('the result reports the swing',
              ack['ngAreanResult'].iBeforeMatchPoint == before
              and ack['ngAreanResult'].iAfterMatchPoint == after)
        check('a win pays out', len(ack['_vecReward']) > 0, ack['_vecReward'])
        check('the win is banked', arena.record(Player.load(3001))['wins'] == 1)

        lost_from = arena.record(alice)['points']
        t2 = arena.record(alice)['matches'][1]
        call(s, 30058, iWinLose=0, iMyTotalPower=4000, iMatchTotalPower=t2['power'],
             _iMatchUserAccount=t2['account_id'], _iMatchIndex=t2['slot'],
             dungeonID=0, strRecordInfo='', AuthEnable=False, _LogString='')
        check('a loss costs rating',
              arena.record(Player.load(3001))['points'] < lost_from,
              (lost_from, arena.record(Player.load(3001))['points']))
        check('the loss is banked', arena.record(Player.load(3001))['losses'] == 1)

        ack = call(s, 30055)                                  # battle log
        check('both battles are in the log', len(ack['vecArenaBattleLog']) == 2,
              len(ack['vecArenaBattleLog']))
        ack = call(s, 30056)                                  # leaderboard
        check('the leaderboard is populated', len(ack['vecArenaRankInfo']) > 10,
              len(ack['vecArenaRankInfo']))
        check('the leaderboard is sorted best first',
              all(a.iMatchPoint >= b.iMatchPoint for a, b in
                  zip(ack['vecArenaRankInfo'], ack['vecArenaRankInfo'][1:])))

        ack = call(s, 30059)                                  # reroll
        check('rerolling offers somebody', ack['iError'] == 0, ack.get('iError'))

        print('\nthe enemy actually shows up')
        # The client builds the enemy side from vceUnitPartyInfo: it looks up
        # the ArenaDefense party (ContentsType 5) and places each UnitUID at
        # its SlotIndex. Slots are 1, 2, 10, 11 -- front row then back -- which
        # is what a real client sends and what ArenaAITeamInfo.SlotID holds.
        # SlotType 0 or slots 0..3 put nobody on the field, and the fight is
        # won the instant it starts.
        m = arena.record(alice)['matches'][0]
        mu = arena.match_user(m)
        check('the opponent card carries a team', len(mu.vecUnitInfo) > 0,
              len(mu.vecUnitInfo))
        check('its party rows are the defence party (ContentsType 5)',
              all(r.SlotType == arena.CONTENTS_ARENA_DEFENSE
                  for r in mu.vceUnitPartyInfo),
              [r.SlotType for r in mu.vceUnitPartyInfo])
        check('they sit in real slots, not 0..3',
              all(r.SlotIndex in arena.DEFAULT_SLOTS for r in mu.vceUnitPartyInfo),
              [r.SlotIndex for r in mu.vceUnitPartyInfo])
        check('every party row points at a unit that was sent',
              {r.UnitUID for r in mu.vceUnitPartyInfo}
              == {u.UID for u in mu.vecUnitInfo},
              ([r.UnitUID for r in mu.vceUnitPartyInfo],
               [u.UID for u in mu.vecUnitInfo]))
        check('a bot keeps the slots its table gave it',
              [r.SlotIndex for r in mu.vceUnitPartyInfo]
              == [u['slot'] for u in arena.bot_by_id(m['account_id'])['units']],
              [r.SlotIndex for r in mu.vceUnitPartyInfo])

        print('\ntwo players can find each other')
        bob = Player.create(3002, 'dev-bob')
        for uid in (5, 6, 7, 8):
            bob.add_unit(uid, level=55)
        bob.save()
        sb = FakeSession(bob)
        call(sb, 30054, vecChangePartyInfo=party(bob.d['units'][:4]))
        arena.invalidate_pool()

        # Force matchmaking to prefer people so the test is not a coin flip.
        SETTINGS.set('arena.ai_match_rate', 0)
        SETTINGS.load()
        arena.roll_matches(alice)
        ids = [int(m['account_id']) for m in arena.record(alice)['matches']]
        check('the other player is offered as an opponent', 3002 in ids, ids)

        match = next(m for m in arena.record(alice)['matches']
                     if int(m['account_id']) == 3002)
        check("the opponent card carries the other player's defence team",
              len(arena.opponent_units(match)) == 4,
              len(arena.opponent_units(match)))
        check('their name is shown', match['name'] == bob.nickname, match['name'])

        alice.save()
        before = arena.record(alice)['points']
        call(s, 30057, vecPartyInfo=party(alice.d['units'][:4]),
             _iMatchUserAccountID=3002, _iMatchIndex=match['slot'],
             dungeonID=0, randomSeed=1)
        check('a fight against a real player is accepted',
              s.last()['iError'] == 0, s.last().get('iError'))
        call(s, 30058, iWinLose=1, iMyTotalPower=5000, iMatchTotalPower=match['power'],
             _iMatchUserAccount=3002, _iMatchIndex=match['slot'], dungeonID=0,
             strRecordInfo='', AuthEnable=False, _LogString='')
        check('beating a player moves the rating',
              arena.record(Player.load(3001))['points'] != before)
        check("the defender's own save is untouched by being attacked",
              arena.record(Player.load(3002))['losses'] == 0)
        SETTINGS.set('arena.ai_match_rate', -1)
        SETTINGS.load()

        print('\ndashboard-built bots')
        SETTINGS.set('arena.bots', [{
            'name': 'Custom Squad', 'points': 1500, 'exp': 9000, 'profile': 3,
            'units': [{'id': 41, 'level': 60, 'tier': 5, 'grade': 3, 'rareness': 2},
                      {'id': 3, 'level': 60}],
        }])
        SETTINGS.set('arena.use_shipped_bots', False)
        SETTINGS.load()
        only = arena.bots()
        check('a custom team replaces the shipped ones when asked',
              len(only) == 1 and only[0]['name'] == 'Custom Squad', only)
        check('its units are filled in',
              len(only[0]['units']) == 2 and only[0]['units'][0]['id'] == 41)
        arena.roll_matches(alice)
        check('matchmaking offers it',
              any(m['name'] == 'Custom Squad'
                  for m in arena.record(alice)['matches']),
              [m['name'] for m in arena.record(alice)['matches']])
        SETTINGS.set('arena.use_shipped_bots', True)
        SETTINGS.load()
        check('shipped teams come back', len(arena.bots()) > 800)

        print('\nlimits and seasons')
        SETTINGS.set('arena.daily_battles', 1)
        SETTINGS.load()
        check('a daily cap is reported', arena.plays_left(alice) == 0,
              arena.plays_left(alice))
        SETTINGS.set('arena.daily_battles', 0)
        SETTINGS.load()
        check('0 means unlimited', arena.plays_left(alice) == -1)

        SETTINGS.set('arena.season', 2)
        SETTINGS.load()
        rec = arena.record(Player.load(3001))
        check('a new season resets the ladder',
              rec['season'] == 2 and rec['points'] == 0 and rec['wins'] == 0, rec)

        # -- hand-built teams may not contain anything the battle scene will
        # -- choke on.  A hero at a slot that is not an EUnitPosition throws a
        # -- NullReferenceException inside PlayBaseScene.UnitLoading, which the
        # -- player sees only as a black screen.
        print('\ndashboard bot validation')
        caps = arena.tier_level_caps()
        check('level caps come from TierUpTable',
              caps.get(1) == 30 and caps.get(5) == 80 and caps.get(10) == 110, caps)
        check('only playable heroes are offered',
              len(arena.playable_heroes()) == 138, len(arena.playable_heroes()))

        clean, problems = arena.check_bot({
            'name': 'EvilHaiko', 'points': 1000,
            'units': [{'id': 1, 'level': 60, 'tier': 5, 'grade': 3, 'rareness': 2},
                      {'id': 16, 'level': 62, 'tier': 5, 'grade': 5, 'rareness': 2},
                      {'id': 87, 'level': 1, 'tier': 5, 'grade': 5, 'rareness': 2},
                      {'id': 2, 'level': 12, 'tier': 5, 'grade': 7, 'rareness': 3}]}, 0)
        slots = [u['slot'] for u in clean['units']]
        check('a four-hero team lands on 1/2/10/11, never 3 or 4',
              slots == [1, 2, 10, 11], slots)
        check('all four heroes survive validation',
              [u['id'] for u in clean['units']] == [1, 16, 87, 2], clean['units'])
        check('a star count past 6 is clamped and reported',
              clean['units'][3]['grade'] == 6
              and any('grade must be' in p for p in problems), problems)

        clean, problems = arena.check_bot({
            'name': '', 'points': -5,
            'units': [{'id': 999999},                       # not in UnitList
                      {'id': 300, 'level': 9, 'tier': 1},   # in UnitList, not a hero
                      {'id': 1, 'level': 200, 'tier': 1},   # past the tier's cap
                      {'id': 1, 'level': 60, 'tier': 5},    # the same hero again
                      {'id': 16}, {'id': 2}]}, 1)
        check('an unknown unit id is dropped',
              any('999999' in p for p in problems), problems)
        check('a non-playable unit is dropped',
              any('300' in p for p in problems), problems)
        check('level is capped by tier, not globally',
              clean['units'][0]['level'] == caps[1], clean['units'][0])
        check('a repeated hero is dropped',
              [u['id'] for u in clean['units']].count(1) == 1, clean['units'])
        check('a team is never longer than four',
              len(clean['units']) <= arena.MAX_TEAM, clean['units'])
        check('a nameless team is given a name', clean['name'] == 'Bot 2', clean['name'])
        check('a negative rating is floored at zero', clean['points'] == 0, clean['points'])

        base = arena.playable_heroes()[16][1]
        low = arena.check_bot({'name': 'x', 'units': [{'id': 16, 'rareness': 0}]}, 0)[0]
        check('rarity cannot go below what the hero is born as',
              low['units'][0]['rareness'] == base, (low['units'][0], base))

        # A bad team already sitting in settings.json must be repaired on the
        # way out, not trusted, so an older file cannot black-screen anyone.
        SETTINGS.set('arena.bots', [{'name': 'Legacy', 'units': [
            {'id': 1, 'slot': 3}, {'id': 2, 'slot': 4}]}])
        SETTINGS.set('arena.use_shipped_bots', False)
        SETTINGS.load()
        bot = arena.bots()[0]
        check('a bad slot in settings.json is repaired on read',
              [u['slot'] for u in bot['units']] == [1, 2],
              [u['slot'] for u in bot['units']])
        lineup = arena.opponent_lineup(
            {'bot_index': 0, 'account_id': arena.bot_account_id(bot['index'])})
        check('every opponent reaches the client at a real EUnitPosition',
              all(s in arena.DEFAULT_SLOTS for _u, s in lineup), lineup)

    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    print()
    if FAIL:
        print('%d FAILED, %d passed' % (len(FAIL), len(PASS)))
        for f in FAIL:
            print('   -', f)
        return 1
    print('ALL CHECKS PASSED (%d)' % len(PASS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
