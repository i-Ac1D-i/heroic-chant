"""Stage play: join, start, end, and reward payout."""
import logging
from datetime import datetime

from ..net import handler
from ..protocol.dto import TYPES
from ..data.tables import TABLES, to_int
from ..game import state, rewards, afk
from ..game.errors import Err
from ..game.enums import CollectionType

log = logging.getLogger('hc.dungeon')


def _autoplay_info(p):
    return TYPES['NGDungeonAutoPlayInfo'](**afk.info_fields(p))


@handler(30008)
async def dungeon_scene_join(s, a):
    await s.send(40013, Err.OK, _autoplay_info(s.player),
                 state.resource_sync(s.player),
                 TYPES['NGDungeonAutoPlayFastReward']())


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
    p.d['party'] = [{'slot_type': u.SlotType, 'slot_index': u.SlotIndex,
                     'unit_uid': u.UnitUID, 'skill_on_off': u.SkillOnOff}
                    for u in a['vecPartyInfo']]
    p.d['last_dungeon'] = did
    p.save()
    log.info('stage %d start (cost %s x%d)', did, cost_type, cost_val)
    await s.send(40014, Err.OK, did, a['vecPartyInfo'])


@handler(30010)
async def dungeon_end(s, a):
    p, did = s.player, a['DungeonID']
    # ClearType is *how* the stage was played (Default/Fast/Immediate/Clear),
    # not whether it was won.  There is no retreat or defeat packet anywhere in
    # the 401 C2S ids: a loss simply returns to the lobby and the server never
    # hears about it, with the cost charged back at DungeonStart.  So any
    # DungeonEndReq is a clear.  _StarFlag is the bitmask of star missions met.
    star = a['_StarFlag']
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
    if first:
        ctype = (CollectionType.HardDungeonStarCount
                 if to_int(row.get('modeID'), 0) == 1
                 else CollectionType.NormalDungeonStarCount)
        p.add_collection(ctype, bin(star & 7).count('1') or 1,
                         t2=to_int(row.get('floor'), 0))
    before = len(p.d['units'])
    drops = rewards.roll(did, first_clear=first)
    rewards.grant(p, drops)
    n_new = len(p.d['units']) - before

    star_drops = rewards.star_rewards(did) if first else []
    rewards.grant(p, star_drops)

    # Account rank gates how far heroes can level, so a stage has to grant it.
    cost_type, cost_val = TABLES.dungeon_cost(did)
    exp_total = p.add_user_exp(max(cost_val, 1))

    # Unlock whatever this stage gates.
    now = datetime.utcnow()
    opened = [TYPES['NGDungeonOpenEnable'](
                  ContentsID=to_int(row.get('modeID'), 0),
                  DungeonID=to_int(row['dungeon_ID']),
                  OpenEnableTime=now, ClearTime=now)
              for row in TABLES.json('dungeonlist')
              if to_int(row.get('preDungeonID'), -1) == int(did)]

    p.save()
    log.info('stage %d cleared (first=%s, stars=%d, ClearType=%d): %d drops, %d unlocks',
             did, first, star, a['ClearType'], len(drops), len(opened))

    new_units = [state.unit_info(u) for u in p.d['units'][-n_new:]] if n_new else []
    star_reward_infos = [state.resource_info(*r) for r in star_drops]
    await s.send(40015, Err.OK, _autoplay_info(p),
                 state.resource_sync(p, vecAddUnitInfo=new_units,
                                     vecChangeUserExp=[exp_total]),
                 star_reward_infos, opened)


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
