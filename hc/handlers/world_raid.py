"""World Raid on the game server: WorldRaidGameStartReq (30212) and
WorldRaidGameEndReq (30213).

These serve single play.  Party play also goes through them, but its rooms,
ready-ups and battle sync are the match-server protocol (50000-50024 /
60000-60050), which is not implemented yet.  See hc/game/world_raid.py.
"""
import logging

from ..net import handler
from ..game import state, rewards, world_raid as wr
from ..game.errors import Err

log = logging.getLogger('hc.world_raid')


@handler(30212)
async def world_raid_start(s, a):
    p, did = s.player, int(a['_DungeonID'])
    party = a['_vecPartyInfo'] or []
    err = wr.can_start(p, did)
    if err:
        log.info('world raid %d refused: %d', did, err)
        await s.send(40231, err, did, party, state.resource_sync(p))
        return
    wr.charge_join(p, did)
    if party:
        p.set_party(party[0].SlotType,
                    [{'slot_type': u.SlotType, 'slot_index': u.SlotIndex,
                      'unit_uid': u.UnitUID, 'skill_on_off': u.SkillOnOff} for u in party])
    p.d['world_raid_active'] = did
    p.save()
    log.info('world raid %d start (room %s)', did, a.get('_RoomIndex'))
    await s.send(40231, Err.OK, did, party, state.resource_sync(p))


@handler(30213)
async def world_raid_end(s, a):
    p, did = s.player, int(a['_DungeonID'])
    active = p.d.pop('world_raid_active', None)
    kills = int(a['_MonsterKillCount'])
    paid, daily, extra = [], [], {}
    if active != did or wr.row(did) is None:
        log.info('world raid %d end with no run started -- nothing paid', did)
    else:
        paid, daily = wr.finish(p, did, kills)
        before = len(p.d['units'])
        rewards.grant(p, paid)
        rewards.grant(p, daily)
        extra['vecAddUnitInfo'] = [state.unit_info(u) for u in p.d['units'][before:]]
        if daily:
            extra['vecChangeWorldRaidDailyReward'] = [wr.daily_info(p)]
        log.info('world raid %d end: %d kills, paid %s, daily %s', did, kills, paid, daily)
    p.save()
    await s.send(40232, Err.OK, state.resource_sync(p, **extra),
                 [state.resource_info(*r) for r in paid],
                 [state.resource_info(*r) for r in daily],
                 wr.clear_infos(p))
