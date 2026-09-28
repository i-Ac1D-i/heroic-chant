"""Stage play: join, start, end, and reward payout."""
import logging
from datetime import datetime

from ..net import handler
from ..protocol.dto import TYPES
from ..data.tables import TABLES, to_int
from ..game import state, rewards, afk, stars
from ..game.errors import Err
from ..game.enums import CollectionType

log = logging.getLogger('hc.dungeon')


def _autoplay_info(p):
    return TYPES['NGDungeonAutoPlayInfo'](**afk.info_fields(p))


def open_enables(player):
    """Every dungeon this account can see as open: the ones cleared, plus
    whatever those clears unlock next.

    DungeonEndAck only sends the delta from one clear, so a fresh login never
    hears about older clears - the open condition on things like Command
    Center stays unmet even though cleared/collections are correct. Sent at
    login via NGLogInAck03.vecDungeonOpenEnable.
    """
    now = datetime.utcnow()
    cleared = {int(k) for k in player.d.get('cleared', {})}
    out = []
    for row in TABLES.json('dungeonlist'):
        did = to_int(row['dungeon_ID'])
        if did in cleared or _unlocked(row, cleared):
            out.append(TYPES['NGDungeonOpenEnable'](
                ContentsID=to_int(row.get('modeID'), 0), DungeonID=did,
                OpenEnableTime=now, ClearTime=now))
    return out


def _fast_reward_info(p):
    return TYPES['NGDungeonAutoPlayFastReward'](**afk.fast_info_fields(p))


@handler(30008)
async def dungeon_scene_join(s, a):
    await s.send(40013, Err.OK, _autoplay_info(s.player),
                 state.resource_sync(s.player), _fast_reward_info(s.player))


@handler(30009)
async def dungeon_start(s, a):
    p, did = s.player, a['dungeonID']
    if TABLES.dungeon(did) is None:
        log.warning('unknown dungeon %d', did)
        await s.send(40014, Err.NOT_FOUND, did, a['vecPartyInfo'])
        return

    cost_type, cost_val = TABLES.dungeon_cost(did)
    if cost_type >= 0 and not p.spend_resource(cost_type, cost_val):
        log.info('stage %d refused: not enough resource %d', did, cost_type)
        await s.send(40014, Err.NOT_ENOUGH, did, a['vecPartyInfo'])
        return

    # Remember the party so the client's next load keeps the same line-up.
    p.set_party(a['vecPartyInfo'][0].SlotType if a['vecPartyInfo'] else 0,
                [{'slot_type': u.SlotType, 'slot_index': u.SlotIndex,
                  'unit_uid': u.UnitUID, 'skill_on_off': u.SkillOnOff}
                 for u in a['vecPartyInfo']])
    p.d['last_dungeon'] = did
    p.save()
    log.info('stage %d start (cost %s x%d)', did, cost_type, cost_val)
    await s.send(40014, Err.OK, did, a['vecPartyInfo'])


def _hard_pre(did):
    """The Normal stage a Hard stage waits on (hardStoryDungeonList
    .preDungeonID, e.g. Normal 140 for Hard 5001), or -1.  dungeonlist only
    chains Hard stages to each other, so a Hard stage needs both."""
    r = TABLES.row('hardStoryDungeonList', 'dungeon_ID', int(did), source='json')
    return to_int(r.get('preDungeonID'), -1) if r else -1


def _unlocked(row, cleared):
    """Whether a stage's prerequisites are all in ``cleared``: the stage
    before it (if any), and for a Hard stage its Normal one too."""
    pre = to_int(row.get('preDungeonID'), -1)
    hard_pre = _hard_pre(row['dungeon_ID'])
    if pre < 0 and hard_pre < 0:
        return False                  # the very first stages: nothing to unlock them
    return (pre < 0 or pre in cleared) and (hard_pre < 0 or hard_pre in cleared)


def clear_stage(p, did, star):
    """Record a won story stage (Normal or Hard) and pay for it.  Returns
    (first, drops, star_drops, exp_total, opened, new_units)."""
    first = p.mark_cleared(did, star or 1)

    # DungeonClearCount is what CheckClearDungeon reads, so this is the record
    # that actually unlocks the next stage -- both now and on the next login.
    # Its *value* is the 3-bit star mask, not a play count: the floor's "x/30"
    # counter is the popcount of these masks, so storing a flat 1 per stage
    # showed one star per cleared stage instead of the stars actually earned.
    # OR the mask so a replay can add stars but never take them away.
    prev = p.get_collection(CollectionType.DungeonClearCount, t2=did)
    p.set_collection(CollectionType.DungeonClearCount,
                     (int(prev) | (star & 7)) or 1, t2=did)

    row = TABLES.dungeon(did) or {}
    # The stage's own star mask, which is what the stage list and the floor's
    # x/30 are drawn from -- see hc/game/stars.py.  Each star's one-off reward
    # is paid the first time that star is earned, not all three on first clear.
    new_stars = stars.record_stage(p, row, star) if row else 0
    before = len(p.d['units'])
    drops = rewards.roll(did, first_clear=first)
    rewards.grant(p, drops)
    n_new = len(p.d['units']) - before

    star_drops = stars.unpaid_stage_rewards(p, did, new_stars)
    rewards.grant(p, star_drops)

    # Account rank gates how far heroes can level, so a stage has to grant it.
    cost_type, cost_val = TABLES.dungeon_cost(did)
    exp_total = p.add_user_exp(max(cost_val, 1))

    # Unlock whatever this stage gates.
    now = datetime.utcnow()
    cleared = {int(k) for k in p.d.get('cleared', {})}
    opened = [TYPES['NGDungeonOpenEnable'](
                  ContentsID=to_int(row.get('modeID'), 0),
                  DungeonID=to_int(row['dungeon_ID']),
                  OpenEnableTime=now, ClearTime=now)
              for row in TABLES.json('dungeonlist')
              if int(did) in (to_int(row.get('preDungeonID'), -1), _hard_pre(row['dungeon_ID']))
              and _unlocked(row, cleared)]
    new_units = [state.unit_info(u) for u in p.d['units'][-n_new:]] if n_new else []
    return first, drops, star_drops, exp_total, opened, new_units


@handler(30010)
async def dungeon_end(s, a):
    p, did = s.player, a['DungeonID']
    # ClearType is *how* the stage was played (Default/Fast/Immediate/Clear),
    # not whether it was won.  There is no retreat or defeat packet anywhere in
    # the 401 C2S ids: a loss simply returns to the lobby and the server never
    # hears about it, with the cost charged back at DungeonStart.  So any
    # DungeonEndReq is a clear.  _StarFlag is the bitmask of star missions met.
    star = a['_StarFlag']
    first, drops, star_drops, exp_total, opened, new_units = clear_stage(p, did, star)
    p.save()
    log.info('stage %d cleared (first=%s, stars=%d, ClearType=%d): %d drops, %d unlocks',
             did, first, star, a['ClearType'], len(drops), len(opened))

    star_reward_infos = [state.resource_info(*r) for r in star_drops]
    await s.send(40015, Err.OK, _autoplay_info(p),
                 state.resource_sync(p, vecAddUnitInfo=new_units,
                                     vecChangeUserExp=[exp_total]),
                 star_reward_infos, opened)


# Hard story stages (modeID 13).  Read off the client:
#  * HardDungeonTeamSelectSceneInit.Start shows the Hard ticket count
#    (ResourceType 123, 10 a day from ResourceRefresh), and
#    NGNetGameServer.HardDungeonStartAck @0x14F83B8 answers error -468 with
#    the "buy Hard tickets" popup -- so the start is where a missing ticket is
#    refused.
#  * HardDungeonPlayScene.GameEnd always sends HardDungeonEndReq, won or lost,
#    with ClearType the result (1 = win) and the star mask.
#  * hardStoryDungeonList has no entry cost, only FailCost (15 stamina).
# INFERRED: a win uses one Hard ticket, a loss costs the FailCost instead.
HARD_TICKET = 123
NO_HARD_TICKET = -468


def _hard_row(did):
    return TABLES.row('hardStoryDungeonList', 'dungeon_ID', int(did), source='json')


@handler(30158)
async def hard_dungeon_start(s, a):
    p, did = s.player, int(a['dungeonID'])
    party = a['vecPartyInfo'] or []
    if _hard_row(did) is None or TABLES.dungeon(did) is None:
        log.warning('unknown hard stage %d', did)
        await s.send(40174, Err.NOT_FOUND, did, party, state.resource_sync(p))
        return
    if p.get_resource(HARD_TICKET) < 1:
        log.info('hard stage %d refused: no Hard ticket', did)
        await s.send(40174, NO_HARD_TICKET, did, party, state.resource_sync(p))
        return
    if party:
        p.set_party(party[0].SlotType,
                    [{'slot_type': u.SlotType, 'slot_index': u.SlotIndex,
                      'unit_uid': u.UnitUID, 'skill_on_off': u.SkillOnOff} for u in party])
    p.d['hard_active'] = did
    p.save()
    log.info('hard stage %d start', did)
    await s.send(40174, Err.OK, did, party, state.resource_sync(p))


@handler(30159)
async def hard_dungeon_end(s, a):
    p, did = s.player, int(a['DungeonID'])
    active = p.d.pop('hard_active', None)
    won = int(a['ClearType']) == 1
    star_drops, opened, extra = [], [], {}
    if active != did:
        log.info('hard stage %d end with no battle started -- nothing paid', did)
    elif won:
        p.spend_resource(HARD_TICKET, 1)
        first, drops, star_drops, exp_total, opened, new_units = \
            clear_stage(p, did, int(a['_StarFlag']))
        extra = dict(vecAddUnitInfo=new_units, vecChangeUserExp=[exp_total])
        log.info('hard stage %d cleared (first=%s, stars=%d): %d drops, %d unlocks',
                 did, first, int(a['_StarFlag']), len(drops), len(opened))
    else:
        r = _hard_row(did)
        cost_t, cost = to_int(r.get('FailCost_Type1'), -1), to_int(r.get('FailCost_Val1'), 0)
        if cost_t >= 0 and cost > 0:
            p.spend_resource(cost_t, min(cost, p.get_resource(cost_t)),
                             to_int(r.get('FailCost_Type2'), -1))
        log.info('hard stage %d lost (fail cost %d x%d)', did, cost_t, cost)
    p.save()
    await s.send(40175, Err.OK, state.resource_sync(p, **extra),
                 [state.resource_info(*r) for r in star_drops], opened)


@handler(30220)
async def get_dungeon_star_reward(s, a):
    """Open one of the three star chests under a story floor.

    Sent by StarFloorRewardBox with the stage's modeType (0 Normal, 13 Hard),
    story season, floor and the chest's GoalCount.  Paid from starSystemReward
    (the dungeonID -1 rows) once the floor's stars reach the goal, and marked
    in collection 31/32 with the season's flag -- the same record the client
    reads to show the chest as opened.  See hc/game/stars.py.
    """
    p = s.player
    mode, season = int(a['_ModeID']), int(a['_StorySeason'])
    floor, count = int(a['_Floor']), int(a['_StarCount'])

    def reply(err, **extra):
        return s.send(40242, err, mode, season, floor, count,
                      state.resource_sync(p, **extra))

    rows = stars.chest_rows(mode, season, floor, count)
    if not rows or season not in stars.SEASON_FLAG:
        log.info('no star chest for mode %d season %d floor %d at %d stars',
                 mode, season, floor, count)
        await reply(Err.NOT_FOUND)
        return
    if stars.chest_claimed(p, mode, season, floor, count):
        await reply(Err.INVALID)
        return
    have = stars.floor_stars(p, mode, season, floor)
    if have < count:
        log.info('floor %d chest needs %d stars, has %d', floor, count, have)
        await reply(Err.LOCKED)
        return
    payout = stars.chest_reward(rows)
    rewards.grant(p, payout)
    stars.mark_chest(p, mode, season, floor, count)
    p.save()
    log.info('floor %d (mode %d, season %d) star chest %d opened: %s',
             floor, mode, season, count, payout)
    await reply(Err.OK)


@handler(30268)
async def dungeon_autoplay_fast_reward(s, a):
    """City Search "Speed Acquired": buy `_count` lots of 2 hours' loot.

    It had no handler, so the purchase never came back.  Costs and limits are
    fastRewardTime's; see hc/game/afk.py.
    """
    p = s.player
    count = int(a['_count'])
    costs = afk.fast_cost(p, count) if count > 0 else None
    if costs is None:
        log.info('fast reward: %d more would pass the daily %d', count, afk.fast_max())
        await s.send(40290, Err.INVALID, _fast_reward_info(p), state.resource_sync(p))
        return
    for t1, t2, v in costs:
        have = p.total_cash() if t1 == p.TOTAL_CASH else p.get_resource(t1, t2)
        if have < v:
            log.info('fast reward: not enough %d:%d (%d < %d)', t1, t2, have, v)
            await s.send(40290, Err.NOT_ENOUGH, _fast_reward_info(p),
                         state.resource_sync(p))
            return
    for t1, t2, v in costs:
        p.spend_resource(t1, v, t2)
    drops = afk.fast_claim(p, count)
    before = len(p.d['units'])
    rewards.grant(p, drops)
    new_units = [state.unit_info(u) for u in p.d['units'][before:]]
    p.save()
    log.info('fast reward x%d on stage %d (paid %s) -> %d reward kinds',
             count, afk.farm_dungeon(p), costs, len(drops))
    await s.send(40290, Err.OK, _fast_reward_info(p),
                 state.resource_sync(p, vecAddUnitInfo=new_units))


@handler(30011)
async def dungeon_autoplay_reward(s, a):
    """City Search claim -- the idle farm."""
    p = s.player
    hours = afk.elapsed_hours(p)
    drops = afk.claim(p)
    before = len(p.d['units'])
    rewards.grant(p, drops)
    new_units = [state.unit_info(u) for u in p.d['units'][before:]]
    p.save()
    log.info('city search: %.1fh on stage %d -> %d reward kinds',
             hours, afk.farm_dungeon(p), len(drops))
    await s.send(40016, Err.OK, _autoplay_info(p),
                 state.resource_sync(p, vecAddUnitInfo=new_units))
