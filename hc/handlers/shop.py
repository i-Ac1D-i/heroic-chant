"""Shop listings and purchases."""
import logging

from ..net import handler
from ..data.tables import to_int
from ..game import state, shop, rewards
from ..game.errors import Err

log = logging.getLogger('hc.shop')


@handler(30107)
async def get_script_shop_info(s, a):
    info, goods_info, count_price = shop.shop_tables()
    log.info('shop: %d listings, %d goods rows', len(info), len(goods_info))
    await s.send(40119, info, goods_info, count_price)


@handler(30108)
async def buy_shop_goods(s, a):
    p = s.player
    goods_uid, count = a['_GoodsUID'], max(a['_count'], 1)

    row = shop.listing(goods_uid)
    if row is None:
        log.warning('no active shop listing for goods %s', goods_uid)
        await s.send(40120, Err.NOT_FOUND, state.resource_sync(p))
        return

    bought = p.shop_bought(goods_uid)
    price = []
    for n in range(count):
        price += shop.cost(row, bought + n + 1)

    # Fold duplicate currencies so a multi-buy is charged once per type.
    merged = {}
    for t1, t2, val in price:
        merged[(t1, t2)] = merged.get((t1, t2), 0) + val
    costs = [(t1, t2, v) for (t1, t2), v in merged.items()]

    if any(p.get_resource(t1, t2) < v and t1 != p.TOTAL_CASH for t1, t2, v in costs):
        log.info('shop: cannot afford goods %s (%s)', goods_uid, costs)
        await s.send(40120, Err.NOT_ENOUGH, state.resource_sync(p))
        return
    for t1, t2, v in costs:
        if not p.spend_resource(t1, v, t2):
            log.info('shop: cannot afford goods %s (%s)', goods_uid, costs)
            await s.send(40120, Err.NOT_ENOUGH, state.resource_sync(p))
            return

    payout = [(t1, t2, t3, val * count) for t1, t2, t3, val in shop.goods(goods_uid)]
    before = len(p.d['units'])
    rewards.grant(p, payout)
    new_units = [state.unit_info(u) for u in p.d['units'][before:]]
    p.add_shop_bought(goods_uid, count)
    p.save()
    log.info('shop: bought goods %s x%d for %s -> %s',
             goods_uid, count, costs, payout)

    await s.send(40120, Err.OK, state.resource_sync(
        p, vecAddUnitInfo=new_units,
        vecChangeShopGoods=[shop.shop_goods(p, goods_uid)]))


@handler(30134)
async def get_limited_shop_info(s, a):
    """The Store screen's limited/period tab. Serves the same listings; the
    client filters them by their own period windows."""
    from ..protocol.dto import TYPES
    info, goods_info, _ = shop.shop_tables()
    await s.send(40151, info, goods_info, [], TYPES['NGOrdealTowerShop']())
