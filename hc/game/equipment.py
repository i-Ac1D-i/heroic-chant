"""Gear, runes and the item inventory.

Gear is *stackable* and lives in the ordinary resource wallet as
``ResourceType.Item`` (5) keyed by itemID -- the same channel stage drops use
(`rewardClear` rows with `resource_type1 = 5`).  What a unit is wearing is a
separate per-unit map of slot -> itemID, replayed to the client through
``NGUnitInfo.vecEquipInfo`` as ``NGUnitEquipInfo(UnitUID, ItemType, ItemKey)``.

`ItemType` is the slot (1-10, see `itemSlotOpenState`; 9 and 10 are gated on
clearing a dungeon) and `ItemKey` is the itemID sitting in it.

Because gear stacks, equipping has to move an item *out* of the wallet or one
copy could be worn by every hero at once.  `equip`/`unequip` below keep that
balanced; flip ``CONSUME_ON_EQUIP`` if you would rather gear be free.
"""
from ..data.tables import TABLES, to_int
from .enums import ResourceType

CONSUME_ON_EQUIP = True
ITEM = ResourceType.Item          # 5


def item(item_id):
    return TABLES.row('itemList', 'itemID', item_id)


def rune(rune_id):
    return TABLES.row('runeList', 'runeID', rune_id)


def slot_of(item_id):
    """The slot an item belongs in, or -1 if we do not know it."""
    row = item(item_id)
    return to_int(row.get('itemType'), -1) if row else -1


def sell_price(t1, t2):
    """SellGold from ResourceTable for one (Type1, Type2) pair."""
    for r in TABLES.rows('ResourceTable', 'ResourceID', t1, source='json'):
        if to_int(r.get('Type2'), -1) == int(t2):
            return max(to_int(r.get('SellGold'), 0), 0)
    return 0


def grade_up_recipe(item_id):
    """How to fuse this item into the next grade, or None if it is the top.

    ``{material, count, gold, diamond, result, ratio}`` -- ratio is per-mille,
    and a failed roll still eats the materials.
    """
    row = item(item_id)
    if not row:
        return None
    result = to_int(row.get('result_itemID'), -1)
    if result < 0:
        return None
    return {
        'material': to_int(row.get('req_itemID'), int(item_id)),
        'count': max(to_int(row.get('req_itemCount'), 0), 1),
        'gold': to_int(row.get('gradeUp_gold'), 0),
        'diamond': to_int(row.get('gradeUp_Diamond'), 0),
        'result': result,
        'ratio': to_int(row.get('SuccessRatio'), 1000),
    }


def rune_cost(table, grade_up_id, fix=False):
    """Cost of one rune step from runeGradeUp / runeChange.

    Returns ``(costs, result_id)`` where costs is a list of
    ``(type1, type2, amount)``, or ``(None, None)`` if there is no such step.
    `fix` is the client's "guarantee the result" toggle, which adds a gem cost.
    """
    r = TABLES.row(table, 'gradeupid', grade_up_id)
    if r is None:
        return None, None
    costs = [(ResourceType.Gold, -1, to_int(r.get('gold'), 0)),
             (ResourceType.RuneUpMet, -1, to_int(r.get('runeUp_Material'), 0))]
    if fix and to_int(r.get('fix_diamond'), 0) > 0:
        costs.append((ResourceType.Cash, -1, to_int(r['fix_diamond'])))
    return [c for c in costs if c[2] > 0], to_int(r.get('resultID'), 0)
