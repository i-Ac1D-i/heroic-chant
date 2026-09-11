"""Relics (Scene Cards) and the Forge.

"Relic" is the English UI's name for what the protocol calls a Scene Card --
see `hc/game/scenecards.py` for why that is not a guess. These are not the
Artifacts in `handlers/equipment.py`.

The Forge is a four-slot crafting bench:

    30154 SceneCardSlotOpenReq            open a craft slot
    30155 SceneCardManufactureReq         start a craft in it
    30156 SceneCardSlotImmediateCommitReq finish it now
    30157 SceneCardSlotRewardReq          collect the relic

plus, away from the bench:

    30106 SceneCardLevelUpReq             feed relics to a relic
    30161 SceneCardSellReq                sell them
    30183 SceneCardChangeLockReq          lock one so it cannot be spent

The client has a named error for every way each of these can go wrong -- see
strings 11564..11585 -- so the refusal paths here are the game's own rules, not
invented ones.
"""
import logging
from datetime import datetime

from ..net import handler
from ..data.tables import to_int
from ..game import state, scenecards, rewards
from ..game.errors import Err

log = logging.getLogger('hc.relic')


def _slot_ack(p, slot_index):
    return scenecards.slot_info(slot_index, p.forge_slot(slot_index))


@handler(30154)
async def scenecard_slot_open(s, a):
    """Open a craft slot.  Slot 1 is free; 2 and 3 cost gold, 4 costs cash.

    "You do not satisfy the requirements to open this Craft Slot.(-451)" and
    "This Craft Slot is closed.(-453)" are the client's own words for the two
    refusals below.
    """
    p = s.player
    slot_index = int(a['_SlotIndex'])
    if scenecards.slot_row(slot_index) is None:
        log.info('no such craft slot: %s', slot_index)
        await s.send(40170, Err.INVALID, _slot_ack(p, slot_index),
                     state.resource_sync(p))
        return
    if p.forge_slot_open(slot_index):
        log.info('craft slot %d is already open', slot_index)
        await s.send(40170, Err.OK, _slot_ack(p, slot_index),
                     state.resource_sync(p))
        return

    cost = scenecards.slot_open_cost(slot_index)
    if cost is not None:
        t1, t2, amount = cost
        if not p.spend_resource(t1, amount, t2):
            log.info('craft slot %d needs %d of resource %d/%d',
                     slot_index, amount, t1, t2)
            await s.send(40170, Err.NOT_ENOUGH, _slot_ack(p, slot_index),
                         state.resource_sync(p))
            return

    p.forge_slot(slot_index)['open'] = True
    p.save()
    log.info('craft slot %d opened (paid %s)', slot_index, cost)
    await s.send(40170, Err.OK, _slot_ack(p, slot_index),
                 state.resource_sync(p))


@handler(30155)
async def scenecard_manufacture(s, a):
    """Start a craft.

    `_UnitPieceCost` names the hero whose Memory is being spent: Type2 is the
    unit id and Value1 how many.  Which `sceneCardCreateCost` row applies is
    decided by that count -- `UnitPieceCount` is 2 / 5 / 8 / 18 / 30 for craft
    levels 1..5 -- so the client picks the level by choosing how much to spend.
    """
    p = s.player
    slot_index = int(a['_SlotIndex'])
    want = a['_UnitPieceCost']
    unit_id = int(getattr(want, 'Type2', -1))
    pieces = int(getattr(want, 'Value1', 0))

    if not p.forge_slot_open(slot_index):
        log.info('craft slot %s is not open', slot_index)
        await s.send(40171, Err.INVALID, _slot_ack(p, slot_index),
                     state.resource_sync(p))
        return
    pending = p.forge_slot(slot_index)
    if pending.get('card'):
        # "Craft is already in progress in this slot.(-457)"
        log.info('craft slot %d is already busy', slot_index)
        await s.send(40171, Err.INVALID, _slot_ack(p, slot_index),
                     state.resource_sync(p))
        return

    level = _level_for_pieces(pieces)
    row = scenecards.create_cost(level)
    if row is None or unit_id < 0:
        log.info('no craft level for %d piece(s) of unit %s', pieces, unit_id)
        await s.send(40171, Err.INVALID, _slot_ack(p, slot_index),
                     state.resource_sync(p))
        return

    need_pieces = to_int(row.get('UnitPieceCount'), 0)
    kit_type = to_int(row.get('Cost_ResourceType_1'), -1)
    kit_t2 = to_int(row.get('Cost_ResourceType_2'), -1)
    kit_amount = to_int(row.get('Cost_ResourceVal_1'), 0)

    if p.get_resource(scenecards.MEMORY, unit_id) < need_pieces:
        log.info('craft needs %d Memory of unit %d, player has %d',
                 need_pieces, unit_id, p.get_resource(scenecards.MEMORY, unit_id))
        await s.send(40171, Err.NOT_ENOUGH, _slot_ack(p, slot_index),
                     state.resource_sync(p))
        return
    if kit_type >= 0 and p.get_resource(kit_type, kit_t2) < kit_amount:
        # "You don't have enough Craft materials.(-461)"
        log.info('craft needs %d of resource %d/%d', kit_amount, kit_type, kit_t2)
        await s.send(40171, Err.NOT_ENOUGH, _slot_ack(p, slot_index),
                     state.resource_sync(p))
        return

    p.add_resource(scenecards.MEMORY, -need_pieces, unit_id)
    if kit_type >= 0 and kit_amount > 0:
        p.add_resource(kit_type, -kit_amount, kit_t2)

    card_id, grade = scenecards.roll_card(level, unit_id)
    if card_id is None:
        log.warning('craft level %d rolled grade %d with no card pool',
                    level, grade)
        await s.send(40171, Err.INVALID, _slot_ack(p, slot_index),
                     state.resource_sync(p))
        return

    pending.update({'card': int(card_id), 'grade': int(grade),
                    'end': scenecards.finish_time().isoformat(timespec='seconds'),
                    'open': True})
    p.save()
    log.info('craft slot %d: level %d with unit %d Memory -> relic %d (grade %d)',
             slot_index, level, unit_id, card_id, grade)
    await s.send(40171, Err.OK, _slot_ack(p, slot_index),
                 state.resource_sync(p))


def _level_for_pieces(pieces):
    """The craft level whose `UnitPieceCount` the client just paid.

    Falls back to the highest level it can afford rather than refusing, so a
    client that rounds differently still crafts something sensible.
    """
    best = None
    for lv in scenecards.create_levels():
        row = scenecards.create_cost(lv)
        if to_int(row.get('UnitPieceCount'), 0) == int(pieces):
            return lv
        if to_int(row.get('UnitPieceCount'), 0) <= int(pieces):
            best = lv
    return best


@handler(30156)
async def scenecard_slot_immediate(s, a):
    """Finish a craft now.

    The wait is a server policy value (`forge.craft_seconds`, 0 by default), so
    with the default there is nothing to skip and this just says yes.  "There
    is no Info on this slot with Immediate Craft attempt.(-459)" covers an
    empty slot.
    """
    p = s.player
    slot_index = int(a['_SlotIndex'])
    pending = p.forge_slot(slot_index)
    if not pending.get('card'):
        log.info('nothing is cooking in craft slot %s', slot_index)
        await s.send(40172, Err.INVALID, _slot_ack(p, slot_index),
                     state.resource_sync(p))
        return
    pending['end'] = datetime.utcnow().isoformat(timespec='seconds')
    p.save()
    log.info('craft slot %d finished early', slot_index)
    await s.send(40172, Err.OK, _slot_ack(p, slot_index),
                 state.resource_sync(p))


@handler(30157)
async def scenecard_slot_reward(s, a):
    """Collect the finished relic.

    Refusals are the client's own: "There is no Craft Product as a reward in
    this slot.(-464)" and "The Craft time is not yet complete.(-465)".
    """
    p = s.player
    slot_index = int(a['_SlotIndex'])
    pending = p.forge_slot(slot_index)
    if not pending.get('card'):
        await s.send(40173, Err.INVALID, _slot_ack(p, slot_index),
                     state.resource_sync(p))
        return
    if not scenecards.ready(pending):
        log.info('craft slot %d is not finished yet (ends %s)',
                 slot_index, pending.get('end'))
        await s.send(40173, Err.INVALID, _slot_ack(p, slot_index),
                     state.resource_sync(p))
        return

    relic = p.add_scenecard(pending['card'])
    card_id = pending['card']
    pending.pop('card', None)
    pending.pop('grade', None)
    pending.pop('end', None)
    p.save()
    log.info('craft slot %d collected: relic %d (uid %d)',
             slot_index, card_id, relic['uid'])
    await s.send(40173, Err.OK, _slot_ack(p, slot_index), state.resource_sync(
        p, vecAddSceneCard=[scenecards.info(relic)]))


@handler(30106)
async def scenecard_level_up(s, a):
    """Feed relics to a relic.

    A worn or locked relic is refused as material rather than destroyed --
    "Equipped Relic cannot be used as materials." is string 5281.
    """
    p = s.player
    target = p.find_scenecard(a['_uid'])
    if target is None:
        await s.send(40118, Err.NOT_FOUND, scenecards.info(
            {'uid': 0, 'id': 0}), state.resource_sync(p))
        return

    fodder, gained = [], 0
    for uid in (a.get('vecMaterial') or []):
        if int(uid) == int(target['uid']):
            continue
        mat = p.find_scenecard(uid)
        if mat is None:
            continue
        if int(mat.get('equip', 0) or 0):
            log.info('relic %s is worn, not using it as material', uid)
            continue
        if int(mat.get('lock', 0)):
            log.info('relic %s is locked, not using it as material', uid)
            continue
        gained += scenecards.material_exp(mat)
        fodder.append(mat)

    if not fodder:
        # "Select one or more Relic to be used as material(s)." (5284)
        await s.send(40118, Err.INVALID, scenecards.info(target),
                     state.resource_sync(p))
        return

    before = scenecards.level_of(target)
    target['exp'] = int(target.get('exp', 0)) + gained
    removed = [scenecards.info(m) for m in fodder]
    for m in fodder:
        p.remove_scenecard(m['uid'])
    p.save()
    log.info('relic %s ate %d relic(s) for %d exp -- level %d -> %d',
             target['uid'], len(fodder), gained, before,
             scenecards.level_of(target))
    await s.send(40118, Err.OK, scenecards.info(target), state.resource_sync(
        p, vecChangeSceneCard=[scenecards.info(target)],
        vecDelSceneCard=removed))


@handler(30161)
async def scenecard_sell(s, a):
    """Sell relics for gold, at `sceneCardLevelInfo.SellGold`."""
    p = s.player
    sold, gold = [], 0
    for uid in (a.get('_vecSell') or []):
        r = p.find_scenecard(uid)
        if r is None:
            continue
        if int(r.get('equip', 0) or 0) or int(r.get('lock', 0)):
            log.info('relic %s is worn or locked, not selling it', uid)
            continue
        gold += scenecards.sell_price(r)
        sold.append(r)

    if not sold:
        await s.send(40177, Err.INVALID, state.resource_sync(p))
        return

    removed = [scenecards.info(r) for r in sold]
    for r in sold:
        p.remove_scenecard(r['uid'])
    if gold:
        rewards.grant(p, [(0, -1, -1, gold)])
    p.save()
    log.info('sold %d relic(s) for %d gold', len(sold), gold)
    await s.send(40177, Err.OK, state.resource_sync(
        p, vecDelSceneCard=removed))


@handler(30183)
async def scenecard_change_lock(s, a):
    """Lock or unlock a relic, so it cannot be fed to another or sold."""
    p = s.player
    r = p.find_scenecard(a['uid'])
    if r is None:
        await s.send(40199, Err.NOT_FOUND, state.resource_sync(p))
        return
    r['lock'] = 1 if int(a['LockEnable']) else 0
    p.save()
    log.info('relic %s %s', r['uid'], 'locked' if r['lock'] else 'unlocked')
    await s.send(40199, Err.OK, state.resource_sync(
        p, vecChangeSceneCard=[scenecards.info(r)]))
