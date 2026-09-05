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
from ..settings import SETTINGS
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
    over = SETTINGS.get('gacha.cost', {}).get(str(int(gacha_id)))
    if over:
        key = 'ten' if count >= 10 else 'single'
        return (int(over.get('type1', -1)), int(over.get('type2', -1)),
                int(over.get(key, 0)))
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
    # A dashboard override replaces the table weights, keyed by rareness so it
    # survives the rows being in a different order than you expect.
    over = SETTINGS.get('gacha.rates', {}).get(str(int(gacha_id)))
    if over:
        weights = [max(int(over.get(str(to_int(r.get('Rareness'), 0)), 0)), 0)
                   for r in rows]
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


# A duplicate at this rarity or above pays out the hero's *own* Memory
# (ResourceType 16, Type2 = unit id -- "Bam's Memory") rather than a generic
# Memory Stone, which is what the live game did and what the shards are
# actually for.
OWN_MEMORY_FROM_RARENESS = 2        # 2 = SS, 3 = SSS
OWN_MEMORY_AMOUNT = 40


def _own_memory(unit_id):
    """The hero-specific shard reward for a duplicate.

    40 is not invented: `GradeUpMaterial` charges exactly 40 of
    ResourceType 16 / <unitID> to take that hero from 2 stars to 3 (then 80,
    120, 160 for the grades above), and `GachaUnitPieceRatioView` -- the only
    other gacha table that pays this resource -- also pays 40 a time.  So one
    SS duplicate is worth one star-up, which is a defensible reading of a
    number the retail server never wrote down.  INFERRED; change
    OWN_MEMORY_AMOUNT, or override it per rarity in the dashboard.
    """
    return [(16, int(unit_id), -1, OWN_MEMORY_AMOUNT)]


def grant(player, unit_id, rareness):
    """Hand over what a summon rolled.  Returns (unit or None, [reward, ...]).

    A hero you already own does not become a second copy of that hero -- it
    converts to shards.  Which shards depends on how good the hero was:

    * SS and above -> that hero's own Memory (16 / unit id), see `_own_memory`
    * below that   -> `DecompPieceReturn`, the client's own conversion table,
                      keyed by the duplicate's rarity:
                        Rareness 0 (A) -> ResourceType 45 / 1  x1
                        Rareness 1 (S) -> ResourceType 45 / 2  x1

    `DecompPieceReturn` has no row for rareness 3 at all, so before the
    SS-and-above rule an SSS duplicate paid out nothing.

    A dashboard override (`gacha.duplicate`, keyed by rareness) replaces either
    branch.  Write `"unit"` in the Type2 slot to mean "the hero that was
    actually summoned", which is the only way a fixed table can express a
    hero-specific reward.
    """
    owned = any(int(u['id']) == int(unit_id) for u in player.d['units'])
    if not owned or SETTINGS.get('gacha.duplicates_as_units', False):
        return player.add_unit(unit_id, rareness=rareness), []

    over = SETTINGS.get('gacha.duplicate', {}).get(str(int(rareness)))
    if over:
        out = []
        for row in over:
            t1, t2, t3, amount = list(row) + [-1] * (4 - len(row))
            t2 = int(unit_id) if str(t2).lower() == 'unit' else int(t2)
            out.append((int(t1), t2, int(t3), int(amount)))
        return None, out

    if int(rareness) >= OWN_MEMORY_FROM_RARENESS:
        return None, _own_memory(unit_id)

    row = TABLES.row('DecompPieceReturn', 'Rareness', int(rareness))
    if row is None:
        return None, []
    t1 = to_int(row.get('ResourceType1'), -1)
    if t1 < 0:
        return None, []
    return None, [(t1, to_int(row.get('ResourceType2'), -1), -1,
                   to_int(row.get('ResourceVal1'), 0))]


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
# Dimension Gacha
#
# vecSummon carries the outcome of a summon, waiting for the client to open
# it.  Read off the client rather than guessed:
#
#   NGDimensionGacha.GetGachaCount        @0x1A51FD8  return vecSummon.Count
#   DimensionGachaTabHeroUI.GachaSummon   @0x1A3C4D0  if vecSummon.Count >= 1
#                                                     && !vecSummon[0].SoldOut
#                                                     -> "The previous progress
#                                                     of Portal was not
#                                                     complete." and no packet
#                                                     is sent
#
# So the cycle is: summon -> one unsold cube -> BuyDimensionGachaReq opens it
# -> sold, and the next summon is allowed.  An earlier version pre-rolled three
# cubes at login, hit that refusal, and concluded vecSummon had to stay empty
# forever; that read the symptom right and the rule wrong.

BANNERS = (1, 2)


def dimension(player, gid):
    """One NGDimensionGacha: the banner plus whatever cube is waiting on it."""
    _, last = player.summon_count(gid)
    return TYPES['NGDimensionGacha'](
        ID=int(gid), tmRenewLastGacha=last,
        vecSummon=[summon_slot(s) for s in player.gacha_slots(gid)])


def dimension_gacha(player, gacha_ids=BANNERS):
    """NGLogInAck01.vecDimensionGacha -- the summon screen's banners."""
    return [dimension(player, gid) for gid in gacha_ids]


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
