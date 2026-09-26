"""Accessories: the Equipment Summon, locking and selling.  See hc/game/accessories.py.

Summon, lock, sell, stat reroll and fusion.
"""
import logging
import random

from ..net import handler
from ..game import state, accessories, rewards
from ..game.errors import Err
from ..game.enums import ResourceType

log = logging.getLogger('hc.accessories')



@handler(30205)
async def accessory_gacha(s, a):
    """The Portal's Equipment Summon.  Pays gear (ResourceType 5, into the
    wallet like any gear) and accessories (137, minted -- they go out as
    vecAddAccessoryInfo through the sync)."""
    p, group = s.player, int(a['AcceGachaGroupID'])
    row = accessories.gacha_row(group)
    cost = accessories.gacha_cost(p, group)
    if row is None or cost is None:
        await s.send(40224, Err.NOT_FOUND, state.resource_sync(p), [])
        return
    t1, t2, amount = cost
    if t1 >= 0 and amount > 0:
        have = p.total_cash() if t1 == p.TOTAL_CASH else p.get_resource(t1, t2)
        if have < amount:
            log.info('equipment summon %d refused: needs %d of %d/%d', group, amount, t1, t2)
            await s.send(40224, Err.NOT_ENOUGH, state.resource_sync(p), [])
            return
        p.spend_resource(t1, amount, t2)
    pulls = accessories.roll(int(row['AcceSummonGroup']), int(row['GachaCount']))
    rewards.grant(p, [(t1_, t2_, -1, n) for t1_, t2_, n in pulls])
    p.save()
    log.info('equipment summon %d x%s (paid %d of %d/%d): %s', group, row['GachaCount'],
             amount, t1, t2, ', '.join('%d:%d' % (x[0], x[1]) for x in pulls))
    await s.send(40224, Err.OK, state.resource_sync(p),
                 [state.resource_info(t1_, t2_, -1, n) for t1_, t2_, n in pulls])


@handler(30207)
async def accessory_lock(s, a):
    p = s.player
    acc = p.find_accessory(a['uid'])
    if acc is None:
        await s.send(40226, Err.NOT_FOUND, state.resource_sync(p))
        return
    acc['lock'] = 1 if int(a['LockEnable']) else 0
    p.save()
    await s.send(40226, Err.OK, state.resource_sync(
        p, vecChangeAccessoryInfo=[accessories.info(acc)]))


@handler(30209)
async def accessory_sell(s, a):
    """Sell for ResourceTable's SellGold.  Locked or worn ones are skipped."""
    p = s.player
    gone, gold = [], 0
    for uid in a['_vecSell'] or []:
        acc = p.find_accessory(uid)
        if acc is None or acc.get('lock') or acc.get('equip'):
            continue
        gold += accessories.sell_price(acc)
        p.remove_accessory(uid)
        gone.append(acc)
    if gold:
        p.add_resource(ResourceType.Gold, gold)
    p.save()
    log.info('sold %d accessory(ies) for %d gold', len(gone), gold)
    await s.send(40228, Err.OK if gone else Err.INVALID, state.resource_sync(
        p, vecDelAccessoryInfo=[accessories.info(x) for x in gone]))


@handler(30204)
async def accessory_stat_change(s, a):
    """Reroll the stats, keeping any locked slot.  The result waits until the
    player keeps or drops it (30206); the Ack carries it as the accessory's
    would-be self.  Priced by AccessoryData: LockStatChangeCost if a slot is
    locked, StatChangeCost if not (Essence of Mana)."""
    p = s.player
    acc = p.find_accessory(a['uidBaseAccessory'])
    blank = {'uid': 0, 'id': 0, 'stats': []}
    if acc is None or not acc.get('stats'):
        await s.send(40223, Err.NOT_FOUND, state.resource_sync(p), accessories.info(acc or blank))
        return
    slots = {int(x[0]) for x in acc['stats']}
    locked = [int(x) for x in a['vecLockSlot'] or [] if int(x) in slots]
    cost = accessories.reroll_cost(acc['id'], bool(locked))
    if cost is None:
        await s.send(40223, Err.NOT_FOUND, state.resource_sync(p), accessories.info(acc))
        return
    t1, t2, amount = cost
    if t1 >= 0 and amount > 0 and not p.spend_resource(t1, amount, t2):
        log.info('reroll of %d refused: needs %d of %d/%d', acc['uid'], amount, t1, t2)
        await s.send(40223, Err.NOT_ENOUGH, state.resource_sync(p), accessories.info(acc))
        return
    acc['pending'] = accessories.reroll(acc, locked)
    p.save()
    log.info('accessory %d rerolled (locked %s): %s', acc['uid'], locked, acc['pending'])
    await s.send(40223, Err.OK, state.resource_sync(p),
                 accessories.info(acc, stats=acc['pending']))


@handler(30206)
async def accessory_stat_change_fix(s, a):
    """SelectType 1 keeps the reroll, 0 drops it -- the result popup's Fix
    button sends 1, Cancel and "reroll again" send 0
    (PopupboxAccessoryStatChangeFix.<Start>b__0..2)."""
    p = s.player
    acc = p.find_accessory(a['uidBaseAccessory'])
    if acc is None:
        await s.send(40225, Err.NOT_FOUND, state.resource_sync(p))
        return
    pending = acc.pop('pending', None)
    if pending and int(a['SelectType']) == 1:
        acc['stats'] = pending
        log.info('accessory %d keeps its reroll', acc['uid'])
    p.save()
    await s.send(40225, Err.OK, state.resource_sync(
        p, vecChangeAccessoryInfo=[accessories.info(acc)]))


@handler(30203)
async def accessory_compose(s, a):
    """Fusion: a base and two materials of its grade, and optionally an
    Accessory Abrasive.  See the fusion notes in hc/game/accessories.py."""
    p = s.player
    base = p.find_accessory(a['uidBaseAccessory'])
    mats = [p.find_accessory(u) for u in a['vecMaterialAccessory'] or []]
    supports = [int(x) for x in a['vecAccessorySupport'] or [] if int(x) > 0]
    g = accessories.grade(base['id']) if base else 0

    def refuse(err, why):
        log.info('fusion refused: %s', why)
        return s.send(40222, err, False, accessories.relay_info(p, g),
                      state.resource_sync(p))

    if base is None or len(mats) != 2 or any(m is None for m in mats):
        return await refuse(Err.NOT_FOUND, 'base or materials missing')
    uids = {base['uid']} | {m['uid'] for m in mats}
    if len(uids) != 3:
        return await refuse(Err.INVALID, 'the same accessory twice')
    if any(m.get('lock') or m.get('equip') for m in mats):
        return await refuse(Err.INVALID, 'a material is locked or worn')
    if not all(accessories.can_be_material(base, m) for m in mats):
        return await refuse(Err.INVALID, 'materials must match the base grade and slot')
    ratio = accessories.fusion_ratio(base['id'], supports)
    result_id = accessories.fusion_result(base['id'])
    if ratio < 0 or result_id is None:
        return await refuse(Err.INVALID, 'grade %d cannot be fused' % g)
    for item_id in supports:
        if p.get_resource(accessories.SUPPORT, item_id) < 1:
            return await refuse(Err.NOT_ENOUGH, 'no abrasive %d' % item_id)
    for item_id in supports:
        p.add_resource(accessories.SUPPORT, -1, item_id)

    cap = accessories.relay_max(base['id'])
    used = cap > 0 and accessories.relay(p, g) >= cap
    success = used or random.randrange(1000) < ratio
    gone = list(mats)
    for m in mats:
        p.remove_accessory(m['uid'])
    if success:
        wearer, slot = int(base.get('equip', 0) or 0), int(base.get('slot', 0) or 0)
        p.remove_accessory(base['uid'])
        gone.append(base)
        if wearer:
            unit = p.find_unit(wearer)
            if unit is not None:
                unit.setdefault('equip', {}).pop(str(slot), None)
        made = p.add_accessory(result_id)
        accessories.set_relay(p, g, 0)
        log.info('fusion of %d (grade %d, %d%%%s) succeeded -> %d', base['uid'], g,
                 ratio // 10, ', relay used' if used else '', made['id'])
    else:
        if cap > 0:
            accessories.set_relay(p, g, min(accessories.relay(p, g) + 1, cap))
        log.info('fusion of %d (grade %d, %d%%) failed; relay %d/%d', base['uid'], g,
                 ratio // 10, accessories.relay(p, g), cap)
    p.save()
    await s.send(40222, Err.OK, bool(success), accessories.relay_info(p, g, used),
                 state.resource_sync(p, vecDelAccessoryInfo=[accessories.info(x) for x in gone]))
