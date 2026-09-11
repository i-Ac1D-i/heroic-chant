"""Gear: equipping, upgrading, selling, and runes."""
import logging
import random

from ..net import handler
from ..data.tables import to_int
from ..game import state, equipment, artifacts, scenecards
from ..game.errors import Err
from ..game.enums import ResourceType

log = logging.getLogger('hc.equip')


def _equip_artifact(p, unit, slot, uid, changed):
    """Put relic `uid` in `slot` on `unit`, or empty the slot when uid <= 0.

    Relics are per-instance, so "equipping" is moving ownership rather than
    spending a stack: the relic's own `EquipUnitUID` is what the relic screen
    reads.  Anything already in that slot comes off, and a relic worn by
    another hero is taken from them -- which is what the client's UI offers.
    """
    worn = unit.setdefault('equip', {})
    prev = p.artifact_in_slot(unit['uid'], slot)
    if prev is not None and int(prev['uid']) != uid:
        prev['equip'] = 0
        changed[prev['uid']] = prev

    if uid <= 0:
        worn.pop(str(slot), None)
        return True

    relic = p.find_artifact(uid)
    if relic is None:
        log.info('unit %s cannot equip relic %d: not owned', unit['uid'], uid)
        return False
    if artifacts.slot_of(relic['id']) != slot:
        log.info('relic %d (artifact %s) does not go in slot %d',
                 uid, relic['id'], slot)
        return False

    holder = int(relic.get('equip', 0) or 0)
    if holder and holder != int(unit['uid']):
        other = p.find_unit(holder)
        if other is not None:
            other.setdefault('equip', {}).pop(str(slot), None)
            log.info('relic %d taken off unit %s', uid, holder)

    relic['equip'] = int(unit['uid'])
    worn[str(slot)] = uid
    changed[relic['uid']] = relic
    return True


def _equip_scenecard(p, unit, slot, uid, changed):
    """Put relic `uid` in scene-card slot `slot`, or empty it when uid <= 0.

    Relics are per-instance like artifacts, but unlike artifacts they have
    three interchangeable slots (`EItemType.SceneCardSlot1..3`, 9/10/11) and
    every card's own `itemType` is 9, so the slot is the player's choice rather
    than a property of the card.  The slot is therefore stored on the relic.

    "You cannot equip two identical Relics at the same time." is string 5289,
    so wearing two copies of one card id on one hero is refused here too.
    """
    worn = unit.setdefault('equip', {})
    prev = p.scenecard_in_slot(unit['uid'], slot)
    if prev is not None and int(prev['uid']) != uid:
        prev['equip'] = 0
        prev['slot'] = 0
        changed[prev['uid']] = prev

    if uid <= 0:
        worn.pop(str(slot), None)
        return True

    relic = p.find_scenecard(uid)
    if relic is None:
        log.info('unit %s cannot equip relic %d: not owned', unit['uid'], uid)
        return False

    # Same card id already on this hero in another slot.
    for other in p.scenecards():
        if (int(other.get('equip', 0) or 0) == int(unit['uid'])
                and int(other['uid']) != uid
                and int(other['id']) == int(relic['id'])):
            log.info('unit %s already wears relic %s', unit['uid'], relic['id'])
            return False

    holder = int(relic.get('equip', 0) or 0)
    if holder and holder != int(unit['uid']):
        other_unit = p.find_unit(holder)
        if other_unit is not None:
            other_unit.setdefault('equip', {}).pop(
                str(int(relic.get('slot', 0) or 0)), None)
            log.info('relic %d taken off unit %s', uid, holder)

    relic['equip'] = int(unit['uid'])
    relic['slot'] = int(slot)
    worn[str(slot)] = uid
    changed[relic['uid']] = relic
    return True


@handler(30007)
async def unit_equip_change(s, a):
    """The client sends the whole new layout for the units it touched.

    An ItemKey of 0/-1 means "slot emptied".  Gear stacks in the wallet, so
    equipping takes a copy out and unequipping puts it back -- otherwise one
    sword could be worn by the entire roster.

    Relics come through this same packet but are not gear: ItemType is
    `EItemType.ArtifactWeapon` (7) and ItemKey is the relic's **UID**, which is
    why the field is a long.  Sending them down the gear path looked for that
    UID in the item wallet, found nothing, and silently refused -- so relics
    could never be equipped at all.  They branch off to `_equip_relic`.
    """
    p = s.player
    touched, arts, relics = {}, {}, {}
    for e in a['vecChangeInfo']:
        unit = p.find_unit(e.UnitUID)
        if unit is None:
            log.warning('equip change for unknown unit %s', e.UnitUID)
            continue
        slot, key = int(e.ItemType), int(e.ItemKey)
        worn = unit.setdefault('equip', {})
        prev = to_int(worn.get(str(slot)), 0)
        if prev == key:
            continue

        if slot in artifacts.SLOTS:
            if _equip_artifact(p, unit, slot, key, arts):
                touched[unit['uid']] = unit
            continue

        if slot in scenecards.SLOTS:
            if _equip_scenecard(p, unit, slot, key, relics):
                touched[unit['uid']] = unit
            continue

        if key > 0 and equipment.CONSUME_ON_EQUIP:
            if p.get_resource(equipment.ITEM, key) < 1:
                log.info('unit %s cannot equip item %d: none in inventory',
                         e.UnitUID, key)
                continue
            p.add_resource(equipment.ITEM, -1, key)
        if prev > 0 and equipment.CONSUME_ON_EQUIP:
            p.add_resource(equipment.ITEM, 1, prev)      # old piece goes back

        if key > 0:
            worn[str(slot)] = key
        else:
            worn.pop(str(slot), None)
        touched[unit['uid']] = unit

    p.save()
    if touched:
        log.info('equipment updated on %d unit(s), %d artifact(s) and '
                 '%d relic(s) moved', len(touched), len(arts), len(relics))
    await s.send(40012, Err.OK, a['vecChangeInfo'], state.resource_sync(
        p, vecChangeUnitInfo=state.unit_infos(p, list(touched.values())),
        vecChangeArtifactInfo=[artifacts.info(r) for r in arts.values()],
        vecChangeSceneCard=[scenecards.info(r) for r in relics.values()]))


@handler(30013)
async def item_grade_up(s, a):
    """Fuse `req_itemCount` copies into the next grade, per itemList."""
    p = s.player
    item_id, times = a['itemID'], max(a['count'], 1)
    recipe = equipment.grade_up_recipe(item_id)
    if recipe is None:
        log.info('item %s has no upgrade path', item_id)
        await s.send(40018, Err.INVALID, state.resource_sync(p))
        return

    made = failed = 0
    for _ in range(times):
        if p.get_resource(equipment.ITEM, recipe['material']) < recipe['count']:
            break
        if p.get_resource(ResourceType.Gold) < recipe['gold']:
            break
        if recipe['diamond'] and not p.spend_resource(ResourceType.Cash, recipe['diamond']):
            break
        p.add_resource(equipment.ITEM, -recipe['count'], recipe['material'])
        p.add_resource(ResourceType.Gold, -recipe['gold'])
        # SuccessRatio is per-mille; a failure still eats the materials.
        if random.randrange(1000) < recipe['ratio']:
            p.add_resource(equipment.ITEM, 1, recipe['result'])
            made += 1
        else:
            failed += 1

    p.save()
    log.info('item %s upgrade: %d made, %d failed', item_id, made, failed)
    await s.send(40018, Err.OK if (made or failed) else Err.NOT_ENOUGH,
                 state.resource_sync(p))


@handler(30044)
async def item_sell(s, a):
    """Sell gear back for the SellGold listed in ResourceTable."""
    p = s.player
    gold = 0
    for r in a['vecItemSell']:
        have = p.get_resource(r.Type1, r.Type2, r.Type3)
        n = min(int(r.Value1), have)
        if n <= 0:
            continue
        p.add_resource(r.Type1, -n, r.Type2, r.Type3)
        gold += equipment.sell_price(r.Type1, r.Type2) * n
    if gold:
        p.add_resource(ResourceType.Gold, gold)
    p.save()
    log.info('sold items for %d gold', gold)
    await s.send(40049, Err.OK, state.resource_sync(p))


@handler(30066)
async def equip_item_grade_up(s, a):
    """Upgrade the piece a unit is already wearing, in place."""
    p = s.player
    unit = p.find_unit(a['uidUnit'])
    slot = int(a['itemType'])
    if unit is None:
        await s.send(40071, Err.NOT_FOUND, state.resource_sync(p))
        return
    worn = unit.setdefault('equip', {})
    cur = to_int(worn.get(str(slot)), 0)
    recipe = equipment.grade_up_recipe(cur) if cur else None
    if recipe is None:
        await s.send(40071, Err.INVALID, state.resource_sync(p))
        return
    if p.get_resource(equipment.ITEM, recipe['material']) < recipe['count'] \
            or not p.spend_resource(ResourceType.Gold, recipe['gold']):
        await s.send(40071, Err.NOT_ENOUGH, state.resource_sync(p))
        return
    p.add_resource(equipment.ITEM, -recipe['count'], recipe['material'])
    worn[str(slot)] = recipe['result']
    p.save()
    log.info('unit %s slot %d upgraded %d -> %d', unit['uid'], slot, cur, recipe['result'])
    await s.send(40071, Err.OK, state.resource_sync(
        p, vecChangeUnitInfo=state.unit_infos(p, [unit])))


# --------------------------------------------------------------------- runes
@handler(30015)
async def rune_grade_up(s, a):
    p = s.player
    info, is_fix = a['info'], a['IsFix']
    unit = p.find_unit(info.UnitUID)
    if unit is None:
        await s.send(40020, Err.NOT_FOUND, state.resource_sync(p))
        return
    runes = unit.setdefault('runes', {})
    slot = str(int(info.ItemType))
    grade = to_int(runes.get(slot, {}).get('grade'), 0) if isinstance(runes.get(slot), dict) else 0
    costs, result = equipment.rune_cost('runeGradeUp', grade + 1, fix=is_fix)
    if costs is None:
        await s.send(40020, Err.INVALID, state.resource_sync(p))
        return
    if any(p.get_resource(t1, t2) < v for t1, t2, v in costs):
        await s.send(40020, Err.NOT_ENOUGH, state.resource_sync(p))
        return
    for t1, t2, v in costs:
        p.add_resource(t1, -v, t2)
    runes[slot] = {'rune': int(info.ItemKey), 'grade': grade + 1}
    p.save()
    log.info('unit %s rune slot %s -> grade %d', unit['uid'], slot, grade + 1)
    await s.send(40020, Err.OK, state.resource_sync(
        p, vecChangeUnitInfo=state.unit_infos(p, [unit])))


@handler(30016)
async def rune_change(s, a):
    """Reroll the rune in a slot."""
    p = s.player
    info = a['info']
    unit = p.find_unit(info.UnitUID)
    if unit is None:
        await s.send(40021, Err.NOT_FOUND, state.resource_sync(p))
        return
    costs, _ = equipment.rune_cost('runeChange', 1)
    if costs and any(p.get_resource(t1, t2) < v for t1, t2, v in costs):
        await s.send(40021, Err.NOT_ENOUGH, state.resource_sync(p))
        return
    for t1, t2, v in (costs or []):
        p.add_resource(t1, -v, t2)
    runes = unit.setdefault('runes', {})
    runes[str(int(info.ItemType))] = {'rune': int(info.ItemKey), 'grade': 1}
    p.save()
    await s.send(40021, Err.OK, state.resource_sync(
        p, vecChangeUnitInfo=state.unit_infos(p, [unit])))


@handler(30017)
async def rune_slot_open(s, a):
    p = s.player
    unit = p.find_unit(a['_uid'])
    if unit is None:
        await s.send(40022, Err.NOT_FOUND, state.resource_sync(p))
        return
    unit['rune_slots'] = int(unit.get('rune_slots', 0)) + 1
    p.save()
    log.info('unit %s opened rune slot %d', unit['uid'], unit['rune_slots'])
    await s.send(40022, Err.OK, state.resource_sync(
        p, vecChangeUnitInfo=state.unit_infos(p, [unit])))


@handler(30014)
async def artifact_level_up(s, a):
    """Level a relic by feeding it other relics.

    `vecMaterialArtifact` is the fodder, which is consumed.  MaterialEXP is
    what the client renders as the relic's progress, and it only ever goes up.
    A worn or locked relic is refused as fodder rather than quietly destroyed --
    the client has its own `Error_MaterialArtifactVaildEquipUnitUID` (1159) for
    exactly that, so this is not an invented rule.
    """
    p = s.player
    target = p.find_artifact(a['uidArtifact'])
    if target is None:
        log.info('relic level-up for unknown relic %s', a['uidArtifact'])
        await s.send(40019, Err.NOT_FOUND, state.resource_sync(p))
        return

    fodder, gained = [], 0
    for uid in (a.get('vecMaterialArtifact') or []):
        if int(uid) == int(target['uid']):
            continue
        mat = p.find_artifact(uid)
        if mat is None:
            continue
        if int(mat.get('equip', 0) or 0):
            log.info('relic %s is worn by unit %s, not using it as material',
                     uid, mat['equip'])
            continue
        if int(mat.get('lock', 0)):
            log.info('relic %s is locked, not using it as material', uid)
            continue
        gained += artifacts.feed_value(mat)
        fodder.append(mat)

    if not fodder:
        await s.send(40019, Err.INVALID, state.resource_sync(p))
        return

    target['exp'] = int(target.get('exp', 0)) + gained
    removed = [artifacts.info(m) for m in fodder]
    for m in fodder:
        p.remove_artifact(m['uid'])
    p.save()
    log.info('relic %s ate %d relic(s) for %d exp -> %d',
             target['uid'], len(fodder), gained, target['exp'])
    await s.send(40019, Err.OK, state.resource_sync(
        p, vecChangeArtifactInfo=[artifacts.info(target)],
        vecDelArtifactInfo=removed))
