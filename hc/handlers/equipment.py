"""Gear: equipping, upgrading, selling, and runes."""
import logging
import random

from ..net import handler
from ..data.tables import TABLES, to_int
from ..game import state, equipment, artifacts, scenecards, missions
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
    if not scenecards.slot_open(unit, slot):
        # The client locks the slot until awakening node 10002 (slot 9) or
        # 10004 (slot 10) is open, so this only fires for a client that did
        # not check.  "Can be opened in Awakening Tree" is string 2688.
        log.info('unit %s relic slot %d is locked (needs awaken node %s)',
                 unit['uid'], slot, scenecards.SLOT_OPEN_NODE.get(slot))
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

        if slot == equipment.SKIN_SLOT:
            if key > 0 and p.get_resource(ResourceType.HeroSkin, key) < 1:
                log.info('unit %s cannot equip skin %d: not owned',
                         e.UnitUID, key)
                continue
            if key > 0:
                worn[str(slot)] = key
            else:
                worn.pop(str(slot), None)
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
    item_id, times = a['itemID'], min(max(a['count'], 1), 10000)
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

    missions.record_item_grade_up(p, made)       # Guide: "Upgrade Equipment"
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
    missions.record_item_grade_up(p)             # Guide: "Upgrade Equipment"
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


@handler(30170)
async def artifact_manufacture(s, a):
    """Blacksmith Workshop -- craft a regular Artifact, one of the four Forge
    slots (shares them, and the cooking timer, with the relic craft above).

    `_vecItemCost` is two gear items paid as the recipe, not a chosen recipe
    id. `itemList.artifactCreateGroup` on the item that starts that gear's own
    grade-up chain names the recipe row in `artifactCreateTable` (cost only,
    no result column), and that same item's `setID` is the pool of artifacts
    (`artifactListTable.setID`) the craft can produce. Evidence: feeding item
    1 (setID 1, artifactCreateGroup 1) and item 501 (setID 1, group 1) --
    group 1 costs 200 Craft KIT in artifactCreateTable, and setID 1 has seven
    artifactListTable rows, all grade 1, matching the fed gear's own grade.
    Picked at random from that pool, same as a relic craft's random_pool().
    """
    p = s.player
    slot_index = int(a['_SlotIndex'])
    items = [(int(getattr(c, 'Type2', -1)), max(int(getattr(c, 'Value1', 0)), 1))
             for c in (a.get('_vecItemCost') or [])
             if int(getattr(c, 'Type1', -1)) == equipment.ITEM]

    def _ack(err):
        return s.send(40186, err, scenecards.slot_info(slot_index, p.forge_slot(slot_index)),
                       state.resource_sync(p))

    if not p.forge_slot_open(slot_index):
        log.info('artifact craft slot %s is not open', slot_index)
        await _ack(Err.INVALID)
        return
    pending = p.forge_slot(slot_index)
    if pending.get('card') or pending.get('artifact'):
        log.info('craft slot %d is already busy', slot_index)
        await _ack(Err.INVALID)
        return
    if not items:
        await _ack(Err.INVALID)
        return

    groups, set_ids = set(), set()
    for item_id, _count in items:
        row = TABLES.row('itemList', 'itemID', item_id)
        group = to_int(row.get('artifactCreateGroup'), -1) if row else -1
        if group < 0:
            log.info('item %d has no artifact recipe', item_id)
            await _ack(Err.INVALID)
            return
        groups.add(group)
        set_ids.add(to_int(row.get('setID'), -1))
    if len(groups) != 1 or len(set_ids) != 1:
        log.info('artifact craft materials do not share a recipe: %s', items)
        await _ack(Err.INVALID)
        return
    group, set_id = groups.pop(), set_ids.pop()

    recipe = TABLES.row('artifactCreateTable', 'artifactCreateGroup', group)
    if recipe is None:
        await _ack(Err.INVALID)
        return

    for item_id, count in items:
        if p.get_resource(equipment.ITEM, item_id) < count:
            log.info('artifact craft needs %d of item %d', count, item_id)
            await _ack(Err.NOT_ENOUGH)
            return
    kit_type = to_int(recipe.get('Cost_ResourceType_1'), -1)
    kit_t2 = to_int(recipe.get('Cost_ResourceType_2'), -1)
    kit_amount = to_int(recipe.get('Cost_ResourceVal_1'), 0)
    if kit_type >= 0 and p.get_resource(kit_type, kit_t2) < kit_amount:
        log.info('artifact craft needs %d of resource %d/%d', kit_amount, kit_type, kit_t2)
        await _ack(Err.NOT_ENOUGH)
        return

    pool = [to_int(r['artifactID']) for r in TABLES.sql('artifactListTable')
            if to_int(r.get('setID'), -1) == set_id
            and to_int(r.get('itemType'), -1) in artifacts.SLOTS]
    if not pool:
        log.warning('artifact craft group %d (set %d) has no producible artifacts',
                    group, set_id)
        await _ack(Err.INVALID)
        return

    for item_id, count in items:
        p.add_resource(equipment.ITEM, -count, item_id)
    if kit_type >= 0 and kit_amount > 0:
        p.add_resource(kit_type, -kit_amount, kit_t2)

    result_id = random.choice(pool)
    pending.update({'artifact': result_id,
                    'end': scenecards.finish_time().isoformat(timespec='seconds'),
                    'open': True})
    p.save()
    log.info('artifact craft slot %d: group %d set %d -> artifact %d',
             slot_index, group, set_id, result_id)
    await _ack(Err.OK)


@handler(30186)
async def boss_artifact_manufacture(s, a):
    """Boss Artifact Workshop -- craft a boss artifact from `artifactBossCreate`.

    No slot/cooking step here: `BossArtifactManufactureAck` carries no slot
    info, just Error + the usual state sync, so the craft is immediate.
    `_selectStat` is not read -- this server does not roll random artifact
    stats at all (see artifacts.py), so there is nothing for it to pick.
    """
    p = s.player
    create_id = int(a['_BossCreateID'])
    row = TABLES.row('artifactBossCreate', 'CreateGroup', create_id)
    if row is None:
        log.info('no such boss artifact recipe: %s', create_id)
        await s.send(40202, Err.INVALID, state.resource_sync(p))
        return

    costs = []
    for i in (1, 2, 3):
        t1 = to_int(row.get('Material_%d_Type1' % i), -1)
        t2 = to_int(row.get('Material_%d_Type2' % i), -1)
        val = to_int(row.get('Material_%d_Val1' % i), 0)
        if t1 >= 0 and val > 0:
            costs.append((t1, t2, val))
    gold = to_int(row.get('Gold'), 0)

    if (gold and p.get_resource(ResourceType.Gold) < gold) or any(
            p.get_resource(t1, t2) < v for t1, t2, v in costs):
        log.info('boss artifact craft %d needs %s + %d gold', create_id, costs, gold)
        await s.send(40202, Err.NOT_ENOUGH, state.resource_sync(p))
        return

    for t1, t2, v in costs:
        p.add_resource(t1, -v, t2)
    if gold:
        p.add_resource(ResourceType.Gold, -gold)

    result_id = to_int(row.get('ResultID'), -1)
    made = p.add_artifact(result_id)
    p.save()
    log.info('boss artifact craft %d: made artifact %d (uid %d)',
             create_id, result_id, made['uid'])
    await s.send(40202, Err.OK, state.resource_sync(
        p, vecAddArtifactInfo=[artifacts.info(made)]))


# ----------------------------------------------------- exclusive equipment --
def _can_afford(p, costs):
    for t1, t2, amount in costs:
        have = p.total_cash() if t1 == p.TOTAL_CASH else p.get_resource(t1, t2)
        if have < amount:
            return False
    return True


def _charge(p, costs):
    for t1, t2, amount in costs:
        p.spend_resource(t1, amount, t2)


def _exclusive_grade_up(p, item_id, grade_type, worn_by=None):
    """Raise one exclusive item a grade.  Returns (Err, success, changed unit).

    `worn_by` is the unit wearing it (30217) or None for one in the bag
    (30216).  Both modes are documented in hc/game/equipment.py, with the
    client RVAs they were read from.
    """
    recipe = equipment.exclusive_recipe(item_id)
    if recipe is None:
        log.info('exclusive item %s has no next grade', item_id)
        return Err.INVALID, False, None

    if int(grade_type) == equipment.GRADEUP_FIX:
        need = recipe['fusion']
        if need <= 0:
            log.info('exclusive item %s cannot be fused', item_id)
            return Err.INVALID, False, None
        owned = p.get_resource(equipment.ITEM, item_id)
        have = owned + (1 if worn_by is not None else 0)
        if have < need:
            log.info('fusing exclusive item %s needs %d copies, have %d',
                     item_id, need, have)
            return Err.NOT_ENOUGH, False, None
        from_bag = need - (1 if worn_by is not None else 0)
        p.add_resource(equipment.ITEM, -from_bag, item_id)
        if worn_by is not None:
            worn_by.setdefault('equip', {})[str(equipment.EXCLUSIVE_SLOT)] = \
                recipe['result']
        else:
            p.add_resource(equipment.ITEM, 1, recipe['result'])
        log.info('fused %d x exclusive item %s -> %s', need, item_id,
                 recipe['result'])
        return Err.OK, True, worn_by

    # Normal: Enhance.
    if worn_by is None and p.get_resource(equipment.ITEM, item_id) < 1:
        log.info('no exclusive item %s in the bag to enhance', item_id)
        return Err.NOT_FOUND, False, None
    costs = equipment.enhance_costs(recipe)
    if not _can_afford(p, costs):
        log.info('enhancing exclusive item %s needs %s', item_id, costs)
        return Err.NOT_ENOUGH, False, None
    _charge(p, costs)
    success = random.randrange(1000) < recipe['ratio']
    if success:
        if worn_by is not None:
            worn_by.setdefault('equip', {})[str(equipment.EXCLUSIVE_SLOT)] = \
                recipe['result']
        else:
            p.add_resource(equipment.ITEM, -1, item_id)
            p.add_resource(equipment.ITEM, 1, recipe['result'])
    log.info('enhanced exclusive item %s -> %s: %s (%d/1000, paid %s)',
             item_id, recipe['result'], 'success' if success else 'failed',
             recipe['ratio'], costs)
    return Err.OK, success, worn_by


@handler(30216)
async def exclusive_look_item_grade_up(s, a):
    """Raise an exclusive item in the bag a grade.  Unhandled before."""
    p = s.player
    item_id = int(a['itemID'])
    if not equipment.is_exclusive(item_id):
        await s.send(40238, Err.INVALID, state.resource_sync(p), False)
        return
    err, success, _ = _exclusive_grade_up(p, item_id, a['GradeUpType'])
    if err == Err.OK:
        p.save()
    await s.send(40238, err, state.resource_sync(p), bool(success))


@handler(30217)
async def equip_exclusive_look_item_grade_up(s, a):
    """Raise the exclusive item a hero is wearing a grade.  Unhandled before.

    The client's own subject for this Ack emits the changed NGUnitInfo, so the
    unit goes back in vecChangeUnitInfo whether or not the roll succeeded.
    """
    p = s.player
    unit = p.find_unit(a['baseUnitUID'])
    if unit is None:
        await s.send(40239, Err.NOT_FOUND, state.resource_sync(p), False)
        return
    item_id = to_int((unit.get('equip') or {}).get(str(equipment.EXCLUSIVE_SLOT)), 0)
    if item_id <= 0 or not equipment.is_exclusive(item_id):
        log.info('unit %s wears no exclusive item', unit['uid'])
        await s.send(40239, Err.INVALID, state.resource_sync(p), False)
        return
    err, success, _ = _exclusive_grade_up(p, item_id, a['GradeUpType'],
                                          worn_by=unit)
    if err == Err.OK:
        p.save()
    await s.send(40239, err, state.resource_sync(
        p, vecChangeUnitInfo=state.unit_infos(p, [unit])), bool(success))
