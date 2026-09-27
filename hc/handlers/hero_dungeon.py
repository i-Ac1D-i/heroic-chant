"""Hero Dungeon: HeroDungeonStartReq (30037) and HeroDungeonEndReq (30038).

See hc/game/hero_dungeon.py for the rules as the client has them.
"""
import logging

from ..net import handler
from ..game import state, rewards, hero_dungeon
from ..game.errors import Err

log = logging.getLogger('hc.hero_dungeon')


@handler(30037)
async def hero_dungeon_start(s, a):
    """One ticket, the party remembered; the client starts the fight when the
    Ack comes back OK (HeroDungeonStartAck: AddParty, CheckServerInfo)."""
    p, did = s.player, int(a['dungeonID'])
    party = a['vecPartyInfo'] or []
    err = hero_dungeon.can_enter(p, did)
    if err:
        log.info('hero dungeon %d refused: %d', did, err)
        await s.send(40042, err, party, state.resource_sync(p))
        return
    t1, t2, n = hero_dungeon.TICKET
    if not p.spend_resource(t1, n, t2):
        log.info('hero dungeon %d refused: no ticket', did)
        await s.send(40042, Err.NOT_ENOUGH, party, state.resource_sync(p))
        return
    if party:
        p.set_party(party[0].SlotType,
                    [{'slot_index': u.SlotIndex, 'unit_uid': u.UnitUID,
                      'skill_on_off': u.SkillOnOff} for u in party])
    p.d['hero_dungeon_active'] = did
    p.save()
    log.info('hero dungeon %d start', did)
    await s.send(40042, Err.OK, party, state.resource_sync(p))


@handler(30038)
async def hero_dungeon_end(s, a):
    """A cleared battle (the stage paid for at start), or skips (ClearType 3,
    each stage cleared before, not today, one ticket each).  Pays the stage's
    drops -- first-clear drops the first time -- and marks it cleared today."""
    p = s.player
    active = p.d.pop('hero_dungeon_active', None)
    skip = int(a['ClearType']) == hero_dungeon.CLEAR_SKIP
    t1, t2, n = hero_dungeon.TICKET
    before = len(p.d['units'])
    cleared, paid = [], []
    for did in [int(x) for x in a['dungeonIDVec'] or []]:
        if hero_dungeon.row(did) is None or did in cleared:
            continue
        if hero_dungeon.cleared_today(p, did):
            log.info('hero dungeon %d already cleared today', did)
            continue
        if skip or did != active:
            if skip and not hero_dungeon.cleared_ever(p, did):
                log.info('hero dungeon %d cannot be skipped: never cleared', did)
                continue
            if skip and not p.spend_resource(t1, n, t2):
                log.info('hero dungeon skip stopped at %d: no ticket', did)
                break
        first = hero_dungeon.record_clear(p, did)
        drops = rewards.roll(did, first_clear=first)
        rewards.grant(p, drops)
        paid.extend(drops)
        cleared.append(did)
    p.save()
    new_units = [state.unit_info(u) for u in p.d['units'][before:]]
    log.info('hero dungeon clear %s (%s): %d drops', cleared, 'skip' if skip else 'battle',
             len(paid))
    season = hero_dungeon.today()
    await s.send(40043, Err.OK if cleared else Err.INVALID,
                 state.resource_sync(p, vecAddUnitInfo=new_units),
                 [hero_dungeon.clear_info(did, season) for did in cleared])
