"""Dimension Crack: TrainingTowerStartReq (30041), TrainingTowerEndReq (30042)
and GetTrainingTowerCheckPointRewardReq (30043, the skip).

See hc/game/training_tower.py for the rules as the client has them.
"""
import logging

from ..net import handler
from ..game import state, rewards, refills, training_tower as tt
from ..game.errors import Err

log = logging.getLogger('hc.training_tower')


def _pay(p, drops_by_floor):
    """Grant [(dungeon_id, first_clear)] floor by floor; returns the new units."""
    before = len(p.d['units'])
    for did, first in drops_by_floor:
        rewards.grant(p, rewards.roll(did, first_clear=first))
    return [state.unit_info(u) for u in p.d['units'][before:]]


@handler(30041)
async def training_tower_start(s, a):
    """One ticket of this tower; the Ack names the floor to fight
    (TrainingTowerStartAck: AddParty, then the play scene)."""
    p, g = s.player, int(a['groupID'])
    party = a['vecPartyInfo'] or []
    refills.top_up(p)
    tt.backfill(p)
    r = tt.floor(g, tt.current(p, g)) if g in tt.GROUPS else None
    if r is None:
        log.info('dimension crack tower %d: no floor to play', g)
        await s.send(40046, Err.NOT_FOUND, -1, party)
        return
    did = int(r['dungeon_ID'])
    if not p.spend_resource(tt.TICKET, tt.ticket_cost(r), g):
        log.info('dimension crack tower %d: no ticket', g)
        await s.send(40046, tt.NO_TICKET, did, party)
        return
    if party:
        p.set_party(party[0].SlotType,
                    [{'slot_index': u.SlotIndex, 'unit_uid': u.UnitUID,
                      'skill_on_off': u.SkillOnOff} for u in party])
    idx = tt.current(p, g)
    p.d['training_tower_active'] = {'group': g, 'start': idx}
    p.save()
    log.info('dimension crack tower %d floor %d start (%d)', g, idx, did)
    await s.send(40046, Err.OK, did, party)


@handler(30042)
async def training_tower_end(s, a):
    """The run is over; ``dungeonID`` is the highest floor it cleared (the
    floor before the lost one after a loss).  Every floor from the run's first
    up to it pays once; the tower moves on past the highest checkpoint
    reached."""
    p, g, top_did = s.player, int(a['groupID']), int(a['dungeonID'])
    run = p.d.pop('training_tower_active', None)
    top = tt.index_of(top_did) if tt.group_of(top_did) == g else None
    floors, saved = [], None
    if run is None or int(run['group']) != g:
        log.info('dimension crack tower %d end with no run started -- nothing paid', g)
    elif top is not None:
        for idx in range(int(run['start']), top + 1):
            r = tt.floor(g, idx)
            if r is None:
                break
            did = int(r['dungeon_ID'])
            floors.append((did, tt.record_clear(p, did)))
            if tt.is_save_point(r):
                saved = idx
    if saved is not None:
        nxt = tt.floor(g, saved + 1)
        tt.set_current(p, g, saved + 1 if nxt is not None else saved)
    new_units = _pay(p, floors)
    p.save()
    log.info('dimension crack tower %d end at %s: floors %s paid, now on floor %d',
             g, top, [d for d, _ in floors], tt.current(p, g))
    await s.send(40047, Err.OK, g, top_did,
                 state.resource_sync(p, vecAddUnitInfo=new_units))


@handler(30043)
async def training_tower_skip(s, a):
    """Skip, for one tower or several: a ticket each, and the tower's current
    floor's drops -- what the popup previews.  The tower does not move, and
    one still on floor 1 (no checkpoint saved) cannot skip."""
    p = s.player
    refills.top_up(p)
    tt.backfill(p)
    floors, seen, err = [], set(), Err.INVALID
    for g in [int(x) for x in a['vecGroupID'] or []]:
        if g in seen or g not in tt.GROUPS:
            continue
        seen.add(g)
        idx = tt.current(p, g)
        r = tt.floor(g, idx)
        if r is None:
            continue
        if idx <= 1:
            log.info('dimension crack skip tower %d: no checkpoint saved yet', g)
            err = tt.NO_CHECKPOINT
            continue
        if not p.spend_resource(tt.TICKET, tt.ticket_cost(r), g):
            log.info('dimension crack skip tower %d: no ticket', g)
            err = tt.NO_TICKET
            continue
        floors.append((int(r['dungeon_ID']), False))
    new_units = _pay(p, floors)
    p.save()
    log.info('dimension crack skip: %s', [d for d, _ in floors])
    await s.send(40048, Err.OK if floors else err,
                 state.resource_sync(p, vecAddUnitInfo=new_units))
