"""Arena: asynchronous PvP.

The flow the client drives, in order:

    CheckArenaInfoReq            open the screen -- standing + opponents
    ChangeArenaDefensePartyReq   set the team that defends you
    FindNewArenaBlindMatchUserReq  reroll one opponent
    JoinArenaReq                 accepted a fight, here is my party
    PlayAreanRewardReq           the fight finished, here is the result
    AreanRankInfoReq             the leaderboard
    AreanBattleLogReq            recent battles
    AreanTierRewardReq           claim the tier reward

The battle itself runs entirely in the client, exactly as story stages do:
the server hands over both line-ups and is told the outcome afterwards.  There
is no defeat packet anywhere in the protocol, but unlike a stage, arena *does*
report a loss -- `PlayAreanRewardReq.iWinLose` carries it -- so both outcomes
are handled here.
"""
import logging

from ..net import handler
from ..protocol.dto import TYPES
from ..game import state, arena, rewards
from ..game.errors import Err
from ..settings import SETTINGS

log = logging.getLogger('hc.arena')


def _matches(p, refresh=False):
    """The opponents on offer, rolled on first look and kept until rerolled."""
    rec = arena.record(p)
    if refresh or not rec.get('matches'):
        arena.roll_matches(p)
    return rec['matches']


def _daily_counts(p):
    left = arena.plays_left(p)
    return [TYPES['NGContentsDailyPlayCount'](
        ContentsID=arena.CONTENTS_ID, Season=arena.record(p)['season'],
        PlayCount=arena.plays_today(p))] if left >= 0 else []


@handler(30053)
async def check_arena_info(s, a):
    """Opening the arena screen."""
    p = s.player
    rec = arena.record(p)
    matches = _matches(p)
    p.save()
    log.info('arena: %s at %d points (%s), rank %d, %d opponents',
             p.nickname, rec['points'], arena.tier_name(rec['points']),
             arena.my_rank(p), len(matches))
    await s.send(40058, Err.OK,
                 TYPES['NGRankingInfo'](Season=rec['season'],
                                        Rank=arena.my_rank(p),
                                        Score=rec['points']),
                 arena.player_arena_info(p, arena.my_rank(p)),
                 state.resource_sync(p),
                 False,
                 [arena.match_user(m) for m in matches])


@handler(30054)
async def change_defense_party(s, a):
    """The team that defends this account while its owner is away."""
    p = s.player
    rec = arena.record(p)
    rec['defense'] = [{'slot_type': u.SlotType, 'slot_index': u.SlotIndex,
                       'unit_uid': u.UnitUID, 'skill_on_off': u.SkillOnOff}
                      for u in a['vecChangePartyInfo']]
    p.save()
    arena.invalidate_pool()      # other players should see the new line-up
    log.info('arena: %s set a defence team of %d', p.nickname, len(rec['defense']))
    await s.send(40059, Err.OK, a['vecChangePartyInfo'],
                 arena.player_arena_info(p, arena.my_rank(p)), False)


@handler(30059)
async def find_new_match(s, a):
    """Reroll: swap one opponent for a fresh one."""
    p = s.player
    rec = arena.record(p)
    before = {int(m['account_id']) for m in rec.get('matches', [])}
    arena.roll_matches(p)
    fresh = next((m for m in rec['matches']
                  if int(m['account_id']) not in before), None)
    if fresh is None and rec['matches']:
        fresh = rec['matches'][0]
    p.save()
    if fresh is None:
        log.info('arena: nobody available to match against')
        await s.send(40064, Err.NOT_FOUND, TYPES['NGArenaMatchUser'](),
                     state.resource_sync(p))
        return
    log.info('arena: offering %s (%d points)', fresh['name'], fresh['points'])
    await s.send(40064, Err.OK, arena.match_user(fresh), state.resource_sync(p))


@handler(30057)
async def join_arena(s, a):
    """The player picked an opponent and is about to fight."""
    p = s.player
    match = arena.find_match(p, a['_iMatchUserAccountID'], a['_iMatchIndex'])
    if match is None:
        log.info('arena: no such opponent %s', a['_iMatchUserAccountID'])
        await s.send(40062, Err.NOT_FOUND, TYPES['NGArenaMatchUser'](),
                     a['vecPartyInfo'])
        return
    if arena.plays_left(p) == 0:
        log.info('arena: %s is out of battles for today', p.nickname)
        await s.send(40062, Err.NOT_ENOUGH, arena.match_user(match),
                     a['vecPartyInfo'])
        return

    # Remember the attacking party so the next screen keeps the line-up.
    # Keyed by its own SlotType (6 for arena), so this does not disturb the
    # story party -- every party shares one list, told apart by SlotType.
    if a['vecPartyInfo']:
        p.set_party(a['vecPartyInfo'][0].SlotType,
                    [{'slot_index': u.SlotIndex, 'unit_uid': u.UnitUID,
                      'skill_on_off': u.SkillOnOff} for u in a['vecPartyInfo']])
    p.save()
    log.info('arena: %s attacks %s (%d points, power %d)',
             p.nickname, match['name'], match['points'], match.get('power', 0))
    await s.send(40062, Err.OK, arena.match_user(match), a['vecPartyInfo'])


@handler(30058)
async def play_arena_reward(s, a):
    """The fight is over.  iWinLose is 1 for a win, 0 for a loss."""
    p = s.player
    won = int(a['iWinLose']) == 1
    match = arena.find_match(p, a['_iMatchUserAccount'], a['_iMatchIndex'])
    if match is None:
        # The client can report a result for an opponent we have since
        # rerolled. Take the result rather than lose the player's battle.
        match = {'slot': int(a['_iMatchIndex']), 'account_id': int(a['_iMatchUserAccount']),
                 'bot_index': None, 'name': 'Unknown', 'points': 0, 'exp': 0,
                 'profile': 0, 'power': int(a['iMatchTotalPower']), 'beaten': 0}

    result, payout = arena.resolve(p, match, won, a.get('strRecordInfo') or '')
    touched = rewards.grant(p, payout)
    p.save()
    arena.invalidate_pool()
    rec = arena.record(p)
    log.info('arena: %s %s vs %s -- %d -> %d points (%s)',
             p.nickname, 'won' if won else 'lost', match['name'],
             result.iBeforeMatchPoint, result.iAfterMatchPoint,
             arena.tier_name(rec['points']))

    await s.send(40063, Err.OK, result,
                 state.resource_sync(p, only=touched or None),
                 [state.resource_info(*r) for r in payout],
                 [],                                   # tier reward: claimed separately
                 _daily_counts(p),
                 [arena.match_user(m) for m in rec['matches']])


@handler(30056)
async def rank_info(s, a):
    """The leaderboard.  _MaxPacketCount / _NowPacketCount let the client
    stitch several packets together; one is enough at this size."""
    p = s.player
    board = arena.leaderboard(p, int(SETTINGS.get('arena.leaderboard_size', 100)))
    await s.send(40061, 1, 1,
                 [arena.ranker(row, i) for i, row in enumerate(board, 1)])


@handler(30055)
async def battle_log(s, a):
    await s.send(40060, arena.battle_log(s.player))


@handler(30125)
async def battle_record(s, a):
    """One battle's replay blob, by log index."""
    p = s.player
    want = int(a['LogIndex'])
    for entry in arena.record(p)['log']:
        if int(entry['index']) == want:
            logs = arena.battle_log(p, limit=len(arena.record(p)['log']))
            for dto in logs:
                if dto.iLogIndex == want:
                    await s.send(40143, Err.OK, dto)
                    return
    await s.send(40143, Err.NOT_FOUND, TYPES['NGArenaBattleLog']())


@handler(30061)
async def tier_reward(s, a):
    """Claim the reward for the tier currently held."""
    p = s.player
    rec = arena.record(p)
    tier = arena.tier_number(rec['points'])
    if rec['rewards_taken'] >= tier:
        log.info('arena: %s has already claimed tier %d', p.nickname, tier)
        await s.send(40066, Err.INVALID, arena.player_arena_info(p), state.resource_sync(p))
        return
    payout = []
    for step in range(rec['rewards_taken'] + 1, tier + 1):
        row = next((r for r in arena.tiers()
                    if int(r.get('Tier', 0)) == step), None)
        if row is None:
            continue
        payout.extend(arena.season_rewards(int(row.get('PointMin', 0))))
    rec['rewards_taken'] = tier
    touched = rewards.grant(p, payout)
    p.save()
    log.info('arena: %s claimed up to tier %d -- %d reward(s)',
             p.nickname, tier, len(payout))
    await s.send(40066, Err.OK, arena.player_arena_info(p, arena.my_rank(p)),
                 state.resource_sync(p, only=touched or None))


@handler(30226)
async def party_sync(s, a):
    await s.send(40249, Err.OK)
