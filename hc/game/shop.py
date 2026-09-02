"""The in-game shop, driven by the client's own script tables.

Three tables:

* ``ShopInfo``       -- one row per listing: cost, period, region, open gate
* ``ShopGoodsInfo``  -- what a ``goodsUID`` actually hands over (may be several
                        rows for a bundle)
* ``CountPrice``     -- escalating price by how many times you have already
                        bought that listing this period

Listings are region-scoped (``countryLanguage``: 10 Global, 22/23/41 others)
and most carry a ``periodType`` with a 2021-era date window, so the live shop
was almost entirely time-limited.  We serve the entries flagged ``IsUse`` for
our region; the permanent ones (``periodType == -1``) are the ones that always
show up.
"""
from datetime import datetime

from ..data.tables import TABLES, to_int
from ..protocol.dto import TYPES
from . import state

REGION = 10          # matches config.SERVER_NAME_STRING_ID 34645 = "Global"

# The shop was almost entirely time-limited and every window in the shipped
# tables closed years ago, so serving the real dates leaves the Store empty.
# We advertise a window that is open now instead.  Narrow these if you would
# rather have the shop rotate.
OPEN_FROM = datetime(2020, 1, 1)
OPEN_UNTIL = datetime(2099, 12, 31)


def _active(row, region=REGION):
    return (to_int(row.get('IsUse')) == 1
            and to_int(row.get('countryLanguage'), -1) in (region, -1))


def listings(region=REGION):
    return [r for r in TABLES.json('ShopInfo') if _active(r, region)]


def listing(goods_uid, region=REGION):
    for r in listings(region):
        if to_int(r['goodsUID']) == int(goods_uid):
            return r
    return None


def goods(goods_uid):
    """[(t1, t2, t3, amount), ...] handed over by one listing."""
    idx = TABLES.index('ShopGoodsInfo', 'goodsUID', unique=False)
    return [(to_int(r['resourceType1']), to_int(r['resourceType2']),
             to_int(r['resourceType3']), to_int(r['resourceValue1'], 0))
            for r in idx.get(int(goods_uid), [])]


def count_price(count_price_type, nth):
    """Cost of the nth purchase (1-based) for an escalating listing."""
    rows = [r for r in TABLES.json('CountPrice')
            if to_int(r['countPriceType']) == int(count_price_type)]
    if not rows:
        return None
    rows.sort(key=lambda r: to_int(r['count']))
    pick = rows[-1]
    for r in rows:
        if to_int(r['count']) >= int(nth):
            pick = r
            break
    return [(to_int(pick['costType%d' % i]), -1, to_int(pick['costValue%d' % i], 0))
            for i in (1, 2, 3) if to_int(pick['costType%d' % i], -1) >= 0]


def cost(row, nth=1):
    """What this listing costs on its nth purchase."""
    cpt = to_int(row.get('countPriceType'), -1)
    if cpt >= 0:
        priced = count_price(cpt, nth)
        if priced:
            return priced
    out = []
    for i in (1, 2, 3):
        t1 = to_int(row.get('costType%d' % i), -1)
        val = to_int(row.get('costValue%d' % i), 0)
        if t1 >= 0 and val > 0:
            out.append((t1, -1, val))
    return out


def parse_period(value):
    """periodValue1 is a date for windowed listings and an int otherwise."""
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(value).strip(), fmt), None
        except (ValueError, TypeError):
            continue
    return None, to_int(value, 0)


# ------------------------------------------------------------------- DTOs --


def shop_tables(region=None):
    """(vecShopInfo, vecShopGoodsInfo, vecCountPrice) for LogInAck05 and
    GetScriptShopInfoAck."""
    region = REGION if region is None else region

    info = []
    for r in listings(region):
        when, ival = parse_period(r.get('periodValue1'))
        end, _ = parse_period(r.get('periodValue2'))
        info.append(TYPES['NGScriptShopInfo'](
            ShopUID=to_int(r['shopUID']), Title=to_int(r['title']),
            image=to_int(r['image']), shopType=to_int(r['shopType']),
            GoodsUID=to_int(r['goodsUID']), productType=to_int(r['productType']),
            countPriceType=to_int(r['countPriceType']),
            Cost=state.resource_info(to_int(r['costType1']), to_int(r['costType2']),
                               to_int(r['costType3']), to_int(r['costValue1'], 0)),
            productID='' if to_int(r['productID'], -1) == -1 else str(r['productID']),
            BuyCount=to_int(r['buyCount']), PeriodType=to_int(r['periodType']),
            # Every real sale window closed in 2021-2023, so honouring them
            # would leave the Store empty.  Hand the client a window that is
            # open now; the listing data itself is untouched.
            vecPeriodTime=[OPEN_FROM, OPEN_UNTIL],
            PeriodValue1=ival if ival is not None else 0,
            relationPointReward=to_int(r['relationPointReward'], 0),
            relationLvLimit=to_int(r['relationLvLimit'], 0),
            postType=to_int(r['postType']), postName=to_int(r['postName']),
            postDesc=to_int(r['postDesc']), goodsType=to_int(r['goodsType']),
            openType=to_int(r['openType']), openVal=to_int(r['openVal']),
            price=to_int(r['price'], 0), titleDesc=to_int(r['titleDesc']),
            uiFormType=to_int(r['uiFormType']),
            eventIconType=to_int(r['eventIconType']),
            sellLimitHour=to_int(r['sellLimitHour']),
            clientReqFlag=to_int(r['clientReqFlag'], 0),
            eventIconTitle=to_int(r['eventIconTitle']),
            countControllable=to_int(r['countControllable'], 0)))

    wanted = {to_int(r['goodsUID']) for r in listings(region)}
    goods = [TYPES['NGScriptShopGoodsInfo'](
                 GoodsUID=to_int(g['goodsUID']),
                 itemInfo=state.resource_info(to_int(g['resourceType1']),
                                        to_int(g['resourceType2']),
                                        to_int(g['resourceType3']),
                                        to_int(g['resourceValue1'], 0)))
             for g in TABLES.json('ShopGoodsInfo')
             if to_int(g['goodsUID']) in wanted]

    prices = [TYPES['NGScriptCountPrice'](
                  countPriceType=to_int(c['countPriceType']),
                  count=to_int(c['count']),
                  Cost=state.resource_info(to_int(c['costType1']), to_int(c['costType2']),
                                     to_int(c['costType3']), to_int(c['costValue1'], 0)))
              for c in TABLES.json('CountPrice')]
    return info, goods, prices


def shop_goods(player, goods_uid):
    """NGShopGoods -- how many times this listing has been bought."""
    return TYPES['NGShopGoods'](
        GoodsUID=int(goods_uid), PeriodType=0, Season=0,
        BuyCount=player.shop_bought(goods_uid), tmRegDate=datetime.utcnow())


def all_shop_goods(player):
    return [shop_goods(player, uid) for uid in player.d.get('shop_buys', {})]
