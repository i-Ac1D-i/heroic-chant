"""Unit progression: level up, tier up, grade up, equipment."""
import logging

from ..net import handler
from ..data.tables import TABLES, to_int
from ..game import state
from ..game.errors import Err

log = logging.getLogger('hc.units')


def _costs(row, pairs):
    """Pull (resourceID, type2, value) triples out of a cost table row."""
    out = []
    for rid_key, t2_key, val_key in pairs:
        rid = to_int(row.get(rid_key), -1)
        val = to_int(row.get(val_key), 0)
        if rid < 0 or val <= 0:
            continue
        out.append((rid, to_int(row.get(t2_key), -1) if t2_key else -1, val))
    return out


def _pay(player, costs):
    if any(player.get_resource(r, t2) < v for r, t2, v in costs):
        return False
    for r, t2, v in costs:
        player.add_resource(r, -v, t2)
    return True


@handler(30001)
async def unit_level_up(s, a):
    p = s.player
    u = p.find_unit(a['uid'])
    if u is None:
        await s.send(40006, Err.NOT_FOUND, state.resource_sync(p))
        return
    target = u.get('level', 1) + 1
    row = TABLES.index('LevelUpTable', 'Lv', source='sql').get(target)
    if row is None:
        log.info('unit %d is already at max level %d', u['uid'], u['level'])
        await s.send(40006, Err.INVALID, state.resource_sync(p))
        return
    costs = _costs(row, [('ResourceID_1', None, 'Val_1'),
                         ('ResourceID_2', None, 'Val_2')])
    if not _pay(p, costs):
        await s.send(40006, Err.NOT_ENOUGH, state.resource_sync(p))
        return
    u['level'] = target
    p.save()
    await s.send(40006, Err.OK, state.resource_sync(p, vecChangeUnitInfo=[state.unit_info(u)]))


@handler(30002)
async def unit_tier_up(s, a):
    p = s.player
    u = p.find_unit(a['uid'])
    if u is None:
        await s.send(40007, Err.NOT_FOUND, state.resource_sync(p))
        return
    element = to_int((TABLES.unit(u['id']) or {}).get('ElementID'), 1)
    target = u.get('tier', 1) + 1
    row = next((r for r in TABLES.sql('TierUpTable')
                if to_int(r['Tier']) == target and to_int(r['ElementID']) == element), None)
    if row is None:
        await s.send(40007, Err.INVALID, state.resource_sync(p))
        return
    costs = _costs(row, [('ResourceID_1', None, 'Val_1'),
                         ('ResourceID_2', None, 'Val_2'),
                         ('ResourceID_3', 'ResourceID_3_Type2', 'Val_3')])
    if not _pay(p, costs):
        await s.send(40007, Err.NOT_ENOUGH, state.resource_sync(p))
        return
    u['tier'] = target
    p.save()
    await s.send(40007, Err.OK, state.resource_sync(p, vecChangeUnitInfo=[state.unit_info(u)]))


def grade_up_cost(unit_id, grade):
    """GradeUpMaterial rows for one star-up step: elemental Shards, a Memory
    Stone of the right letter, and from 3 stars on the hero's own Memory."""
    return [(to_int(r['ResourceType1']), to_int(r['ResourceType2']),
             to_int(r['ResourceVal1'], 0))
            for r in TABLES.sql('GradeUpMaterial')
            if to_int(r['unitID']) == int(unit_id) and to_int(r['grade']) == int(grade)]


@handler(30003)
async def unit_grade_up(s, a):
    """Star-up.  Costs come straight from GradeUpMaterial, keyed by the unit's
    *current* grade -- grade N is the row that takes it to N+1."""
    p = s.player
    u = p.find_unit(a['uid'])
    if u is None:
        await s.send(40008, Err.NOT_FOUND, state.resource_sync(p))
        return

    grade = u.get('grade', 1)
    costs = grade_up_cost(u['id'], grade)
    if not costs:
        log.info('unit %d is already at max grade (%d)', u['uid'], grade)
        await s.send(40008, Err.INVALID, state.resource_sync(p))
        return
    if not _pay(p, costs):
        log.info('star-up refused for unit %d: needs %s', u['uid'], costs)
        await s.send(40008, Err.NOT_ENOUGH, state.resource_sync(p))
        return

    u['grade'] = grade + 1
    # Fodder units named by the client are consumed.
    removed = []
    for uid in list(a.get('vecMaterialUnitID') or []):
        victim = p.find_unit(uid)
        if victim is not None and victim is not u:
            p.d['units'].remove(victim)
            removed.append(state.unit_info(victim))
    p.save()
    log.info('unit %d star-up %d -> %d (paid %s)', u['uid'], grade, u['grade'], costs)
    await s.send(40008, Err.OK, state.resource_sync(
        p, vecChangeUnitInfo=[state.unit_info(u)], vecDelUnitInfo=removed))


def awaken_cost(unit_id, parts_id):
    return [(to_int(r['resourceType1']), to_int(r['resourceType2']),
             to_int(r['resourceValue1'], 0))
            for r in TABLES.sql('unitAwakenOpenCost')
            if to_int(r['unitID']) == int(unit_id)
            and to_int(r['partsInfoID']) == int(parts_id)]


def awaken_node_exists(unit_id, parts_id):
    return any(to_int(r['unitID']) == int(unit_id)
               and to_int(r['partsInfoID']) == int(parts_id)
               for r in TABLES.sql('unitAwakenPartsInfo'))


@handler(30004)
async def unit_awaken(s, a):
    """Awakening -- the per-hero skill tree.  Each node is a partsInfoID with
    its own cost in unitAwakenOpenCost (Shards, hero Memory, Fame Point).
    The client sends the nodes it wants opened; unlocked ids are stored on the
    unit and replayed in NGUnitInfo.vecAwakenInfo."""
    p = s.player
    u = p.find_unit(a['uid'])
    if u is None:
        await s.send(40009, Err.NOT_FOUND, state.resource_sync(p))
        return

    opened = u.setdefault('awaken', [])
    wanted = [int(x) for x in (a.get('vecPartsInfoID') or [])]
    for parts_id in wanted:
        if parts_id in opened:
            continue
        if not awaken_node_exists(u['id'], parts_id):
            log.warning('unit %d has no awaken node %d', u['id'], parts_id)
            continue
        costs = awaken_cost(u['id'], parts_id)
        if not _pay(p, costs):
            log.info('awaken refused for unit %d node %d: needs %s',
                     u['uid'], parts_id, costs)
            await s.send(40009, Err.NOT_ENOUGH, state.resource_sync(p))
            return
        opened.append(parts_id)
        log.info('unit %d awakened node %d (paid %s)', u['uid'], parts_id, costs)

    p.save()
    await s.send(40009, Err.OK, state.resource_sync(
        p, vecChangeUnitInfo=[state.unit_info(u)]))


# 30007 UnitEquipInfoChangeReq now lives in handlers/equipment.py
