"""Accessories: the earrings and necklaces, and the Equipment Summon that pays them.

Per-instance, like Relics and Artifacts: an accessory has its own UID and
rolled stats, so it is never a wallet row.  ResourceType 137 is only how a
reward *names* one (Type2 = AccessoryID); granting it mints an instance.  The
dashboard used to add 137 rows to the wallet, which the client never shows --
the "earrings do not show up when added" report -- and login converts any such
rows (`migrate_wallet`).

What the client reads, from dump.cs:

* ``NGAccessoryInfo``: UID, ID, vecEffectInfo, EquipUnitUID, LockEnable.  Each
  effect is (AccessoryUID, SlotNum, StatTypeID, StatEffectValue).  The stat bars
  follow the list order; SlotNum is only compared with the locked slot
  (``AccessoryStatChangeUI.InitWithQuestionMark``), so slots are numbered from
  1 as the artifacts' random stats are, and 0 can mean "none".
* All of them at login in ``NGLoginAckLargeData.vecAccessoryInfo``; a hero's
  worn ones in ``NGUnitInfo.vecAccessory``; changes through
  ``vecAdd/Change/DelAccessoryInfo``.
* Worn through ``UnitEquipInfoChangeReq`` with ItemType
  ``EItemType.Accessory1/2`` (13/14) and the accessory's UID as ItemKey.

Tables: ``AccessoryList`` (itemType 13 / 14 wearable, 16 fusion material;
Grade, StatCount, RandStatGroupID), ``AccessoryRandStatSelect`` (which stats a
group can roll, by Frequency), ``AccessoryRandStatRange`` (value range per
grade and stat).  The Equipment Summon: ``GachaAcceList`` (cost per
AcceGachaGroup), ``GachaAcceInfo`` (ResultType_0..10 weights per summon group)
and ``GachaAcceSummonGroup`` (what each ResultType pays, by Frequency) -- about
three quarters gear (ResourceType 5), a quarter accessories.

INFERRED: stats within one accessory are distinct types; a value is uniform
within its range (whole numbers when both ends are whole); a summon is paid in
Equipment Summon Tickets (152/4) when the player has enough, else in diamonds.
"""
import random

from ..data.tables import TABLES, to_int
from ..protocol.dto import TYPES

RESOURCE = 137                 # ResourceType.Accessory -- names one in a reward
WEAR_SLOTS = (13, 14)          # EItemType.Accessory1 / Accessory2
MATERIAL_TYPE = 16             # EItemType.MaterialAccessory


def row(acc_id):
    return TABLES.row('AccessoryList', 'AccessoryID', int(acc_id), source='sql')


def item_type(acc_id):
    return to_int((row(acc_id) or {}).get('itemType'), -1)


def grade(acc_id):
    return to_int((row(acc_id) or {}).get('Grade'), 1)


def _range(grade_, stat):
    for r in TABLES.sql('AccessoryRandStatRange'):
        if to_int(r['Grade']) == int(grade_) and to_int(r['Stat_Type']) == int(stat):
            return r['Base_Min'], r['Base_Max']
    return None


def roll_value(grade_, stat, rng=random):
    lo_hi = _range(grade_, stat)
    if lo_hi is None:
        return 0.0
    lo, hi = lo_hi
    if lo.lstrip('-').isdigit() and hi.lstrip('-').isdigit():
        return float(rng.randint(int(lo), int(hi)))
    lo, hi = float(lo), float(hi)
    return round(rng.uniform(lo, hi), 4)


def roll_stats(acc_id, keep=None, rng=random):
    """[[slot, stat, value], ...] for a fresh accessory, or a reroll that keeps
    the slots in ``keep`` ({slot: [slot, stat, value]}) as they are."""
    r = row(acc_id)
    if r is None:
        return []
    count = max(to_int(r.get('StatCount'), 0), 0)
    group = to_int(r.get('RandStatGroupID'), -1)
    pool = [(to_int(x['Stat_Type']), max(to_int(x['Frequency'], 0), 0))
            for x in TABLES.sql('AccessoryRandStatSelect')
            if to_int(x['RandStatGroupID']) == group]
    if group < 0 or not pool:
        return []
    keep = keep or {}
    taken = {int(s[1]) for s in keep.values()}
    g = grade(acc_id)
    out = []
    for slot in range(1, count + 1):
        if slot in keep:
            out.append(list(keep[slot]))
            continue
        choices = [(stat, w) for stat, w in pool if stat not in taken and w > 0]
        if not choices:
            break
        stat = rng.choices([c[0] for c in choices], weights=[c[1] for c in choices], k=1)[0]
        taken.add(stat)
        out.append([slot, stat, roll_value(g, stat, rng)])
    return out


def make(uid, acc_id, rng=random):
    return {'uid': int(uid), 'id': int(acc_id), 'stats': roll_stats(acc_id, rng=rng),
            'equip': 0, 'slot': 0, 'lock': 0}


def info(a, stats=None):
    uid = int(a['uid'])
    return TYPES['NGAccessoryInfo'](
        UID=uid, ID=int(a['id']),
        vecEffectInfo=[TYPES['NGAccessoryEffectInfo'](
            AccessoryUID=uid, SlotNum=int(slot), StatTypeID=int(stat),
            StatEffectValue=float(value))
            for slot, stat, value in (a.get('stats') if stats is None else stats)],
        EquipUnitUID=int(a.get('equip', 0) or 0),
        LockEnable=int(a.get('lock', 0) or 0))


def sell_price(a):
    from .equipment import sell_price as table_price
    return table_price(RESOURCE, int(a['id']))


# ------------------------------------------------------------ the summon

def gacha_row(group_id):
    return TABLES.row('GachaAcceList', 'AcceGachaGroup', int(group_id), source='sql')


def gacha_cost(player, group_id):
    """(type1, type2, amount) this summon costs the player right now."""
    r = gacha_row(group_id)
    if r is None:
        return None
    t1 = to_int(r.get('Instant_ResourceType1'), -1)
    t2 = to_int(r.get('Instant_ResourceType2'), -1)
    n = to_int(r.get('Instant_ResourceVal1'), 0)
    if t1 >= 0 and n > 0 and player.get_resource(t1, t2) >= n:
        return t1, t2, n
    return (to_int(r.get('InstantSummon_ResourceType1'), -1),
            to_int(r.get('InstantSummon_ResourceType2'), -1),
            to_int(r.get('InstantSummon_ResourceVal1'), 0))


def roll(summon_group, count, rng=random):
    """[(type1, type2, amount), ...] for ``count`` pulls."""
    weights = None
    for g in TABLES.sql('GachaAcceInfo'):
        if to_int(g['AcceSummonGroup']) == int(summon_group):
            weights = [to_int(g.get('ResultType_%d' % i), 0) for i in range(11)]
            break
    if not weights or sum(weights) <= 0:
        return []
    rows = {}
    for r in TABLES.sql('GachaAcceSummonGroup'):
        if to_int(r['AcceSummonGroup']) == int(summon_group) and r.get('IsUse') == '1':
            rows.setdefault(to_int(r['ResultType']), []).append(r)
    out = []
    for _ in range(max(int(count), 1)):
        live = [(rt, w) for rt, w in enumerate(weights) if w > 0 and rows.get(rt)]
        if not live:
            break
        rt = rng.choices([x[0] for x in live], weights=[x[1] for x in live], k=1)[0]
        pick = rng.choices(rows[rt], weights=[max(to_int(r['Frequency'], 0), 1)
                                             for r in rows[rt]], k=1)[0]
        out.append((to_int(pick['ResourceType1']), to_int(pick['ResourceType2']),
                    max(to_int(pick['ResourceVal1'], 1), 1)))
    return out


def migrate_wallet(player):
    """Old saves: accessories sitting in the wallet as 137 rows become real
    ones.  Returns how many were minted."""
    made = 0
    for key in [k for k in list(player.d.get('resources', {}))
                if k.split(':')[0] == str(RESOURCE)]:
        acc_id = int(key.split(':')[1])
        n = int(player.d['resources'].pop(key, 0) or 0)
        if row(acc_id) is None:
            continue
        for _ in range(max(n, 0)):
            player.add_accessory(acc_id)
            made += 1
    return made
