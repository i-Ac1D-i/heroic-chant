"""Accessories: the Equipment Summon, locking and selling.  See hc/game/accessories.py.

Stat reroll (AccessoryStatChangeReq / ...FixReq) and fusion (AccessoryComposeReq)
are not done yet; until they are, they answer "not open" rather than leave the
screen waiting.
"""
import logging

from ..net import handler
from ..game import state, accessories, rewards
from ..game.errors import Err
from ..game.enums import ResourceType

log = logging.getLogger('hc.accessories')

NOT_OPEN = -723            # "This event is not open at the moment."


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
    await s.send(40223, NOT_OPEN, state.resource_sync(s.player),
                 accessories.info(s.player.find_accessory(a['uidBaseAccessory'])
                                  or {'uid': 0, 'id': 0, 'stats': []}))


@handler(30206)
async def accessory_stat_change_fix(s, a):
    await s.send(40225, NOT_OPEN, state.resource_sync(s.player))


@handler(30203)
async def accessory_compose(s, a):
    from ..protocol.dto import TYPES
    await s.send(40222, NOT_OPEN, False, TYPES['NGAccessoryComposeRelayPoint'](),
                 state.resource_sync(s.player))
