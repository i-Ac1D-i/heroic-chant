"""Hero summoning, driven by the client's own gacha tables.

Four tables cooperate:

* ``GachaList[GachaID]``          -- the cost of a 1x and a 10x pull
* ``GachaInfo[GachaID]``          -- which summon group supplies the results
* ``GachaSummonGroupInfo[group]`` -- ``ResultType_0..10`` weights, per million
* ``unitGachaID[GachaID]``        -- one row per rarity tier, each naming a
                                     ``unitRateGroup`` pool and its ``Rareness``
* ``unitRateGroup[GroupID]``      -- the unit ids in that pool

``ResultType_N`` lines up positionally with the Nth ``unitGachaID`` row for the
banner, so for the standard banner the weights read 10% / 18% / 72% against
rows of Rareness 2 / 1 / 0 -- i.e. SS / S / A at the rates the live game used.
"""
import random

from ..data.tables import TABLES, to_int
from ..protocol.dto import TYPES
from . import state

MILLION = 1_000_000
HERO_BANNER = 2   # the banner whose pools carry the SS/S/A unit rates


def _gacha_row(table, gacha_id, key='GachaID'):
    src = 'sql' if table in TABLES.sql_tables() else 'json'
    return TABLES.row(table, key, gacha_id, source=src)


def _unit_rows(gacha_id):
    """unitGachaID rows for one banner, in table order (that order is the
    ResultType_N index)."""
    return TABLES.rows('unitGachaID', 'GachaID', gacha_id, source='json')


def _pool(group_id):
    return [to_int(r['UnitID'])
            for r in TABLES.rows('unitRateGroup', 'GroupID', group_id, source='json')]


def cost(gacha_id, count):
    """(type1, type2, amount) for a 1x or 10x pull, or None if the banner is free."""
    row = _gacha_row('GachaList', gacha_id)
    if row is None:
        return None
    t1 = to_int(row.get('InstantSummon_ResourceType1'), -1)
    if t1 < 0:
        return None
    t2 = to_int(row.get('InstantSummon_ResourceType2'), -1)
    key = 'InstantSummon_ResourceVal1_10' if count >= 10 else 'InstantSummon_ResourceVal1'
    return t1, t2, to_int(row.get(key), 0)


def roll(gacha_id, count=1, rng=random):
    """Return [(unit_id, rareness), ...] for one summon batch.

    Some banners -- notably banner 1, the Time Cube the summon screen shows by
    default -- have no rows in unitGachaID at all, so on their own they can
    never produce a hero.  Fall back to the standard hero banner's pools so the
    cube actually summons something.
    """
    rows = _unit_rows(gacha_id)
    if not rows and int(gacha_id) != HERO_BANNER:
        gacha_id = HERO_BANNER
        rows = _unit_rows(gacha_id)
    if not rows:
        return []

    info = _gacha_row('GachaInfo', gacha_id)
    group_id = to_int(info.get('EliteSummonGroup'), -1) if info else -1
    weights = []
    if group_id >= 0:
        for g in TABLES.sql('GachaSummonGroupInfo'):
            if to_int(g['SummonGroup']) == group_id:
                weights = [to_int(g.get('ResultType_%d' % i), 0) for i in range(11)]
                break
    # Trim to the tiers this banner actually defines; fall back to a flat roll.
    weights = weights[:len(rows)]
    if sum(weights) <= 0:
        weights = [1] * len(rows)

    out = []
    for _ in range(max(int(count), 1)):
        row = rng.choices(rows, weights=weights, k=1)[0]
        pool = _pool(to_int(row['GroupID']))
        if not pool:
            continue
        unit_id = rng.choice(pool)
        out.append((unit_id, to_int(row.get('Rareness'), 0)))
    return out


def rates(gacha_id):
    """Human-readable pull rates, for logging and sanity checks."""
    rows = _unit_rows(gacha_id)
    info = _gacha_row('GachaInfo', gacha_id)
    if not rows or not info:
        return {}
    group_id = to_int(info.get('EliteSummonGroup'), -1)
    for g in TABLES.sql('GachaSummonGroupInfo'):
        if to_int(g['SummonGroup']) == group_id:
            w = [to_int(g.get('ResultType_%d' % i), 0) for i in range(len(rows))]
            total = sum(w) or 1
            return {'rareness %d' % to_int(r.get('Rareness'), 0):
                    round(100.0 * x / total, 3) for r, x in zip(rows, w)}
    return {}


# --------------------------------------------------------------------------
# Dimension Gacha slots
#
# The summon screen is a *slot* gacha: NGDimensionGacha.vecSummon holds the
# cubes on screen, and tapping one sends BuyDimensionGachaReq(GachaID, slotID).
# With an empty vecSummon there is nothing to tap, which is why "Touch!" did
# nothing.  Slots are pre-rolled and persisted so the same cubes stay put
# between sessions until they are actually bought.

SLOTS = 3


def make_slots(banner=HERO_BANNER, count=SLOTS, rng=random):
    """Pre-roll `count` cubes for a banner: [{slot, gacha, unit, rareness}]."""
    out = []
    for slot, (unit_id, rare) in enumerate(roll(banner, count, rng)):
        out.append({'slot': slot, 'gacha': int(banner),
                    'unit': int(unit_id), 'rareness': int(rare),
                    'sold': False})
    return out


# ------------------------------------------------------------------- DTOs --


def dimension_gacha(player, gacha_ids=(1, 2)):
    """NGLogInAck01.vecDimensionGacha -- the summon screen's banners.

    vecSummon is the list of cubes actually drawn on screen; an empty one
    renders a banner you cannot interact with, and leaves the "(used/max)"
    label as a literal "({0}/{1})".
    """
    out = []
    for gid in gacha_ids:
        _, last = player.summon_count(gid)
        # vecSummon stays empty while idle: a cube left unsold here makes the
        # client refuse to open the screen with "the previous progress of
        # Portal was not complete".
        out.append(TYPES['NGDimensionGacha'](
            ID=int(gid), tmRenewLastGacha=last, vecSummon=[]))
    return out


def summon_slot(s):
    return TYPES['NGDimensionGachaSummon'](
        GachaID=int(s['gacha']), SlotID=int(s['slot']),
        SoldOut=bool(s.get('sold')), SummonIndex=int(s['slot']),
        ProductInfo=state.resource_info(1, int(s['unit']), -1, 1))


def summons_count(player, gacha_id):
    """NGGachaDaySummonsCount -- the "{0}/{1}" counter on the summon cube."""
    n, last = player.summon_count(gacha_id)
    return TYPES['NGGachaDaySummonsCount'](
        GachaID=int(gacha_id), SummonsCount=n, Season=0, LastSummonsTime=last)
