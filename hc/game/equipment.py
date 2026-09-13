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


# ------------------------------------------------------ exclusive equipment --
#
# EItemType.ExclusiveLook (15): the 695 `itemList` rows with itemType 15.
# They stack in the wallet like gear and are worn in slot 15.  Two ways to
# raise one a grade, read off the client rather than guessed:
#
#   Normal / Enhance (ExclusiveLookGradeUp.Normal = 0)
#       PopupboxExclusiveLookItemEnhance, click @0x18D6854, runs the same
#       NMUnit.CheckUpgradeItem @0x1495B78 that gear uses.  That reads
#       req_itemID x req_itemCount (other gear, as material), gradeUp_gold,
#       gradeUp_Diamond and ResourceType_1/2 x ResourceVal_1 -- some chains are
#       paid in gear, others in EventCoin (119).  Then a SuccessRatio roll,
#       per-mille.  A failure still spends the materials.
#
#   Fix / Fusion (ExclusiveLookGradeUp.Fix = 1)
#       PopupboxExclusiveLookItemFusion, click @0x18D776C: copies of *this*
#       item owned (GetResourceLongValue(Item, id)) plus one if it is worn must
#       reach ItemInfo.FusionCount (+0xB8), else Error 1227
#       (Error_ExclusiveLookItemNotEnoughMaterial).  No roll, and nothing else
#       is checked -- no gold.  FusionCount copies are consumed.
EXCLUSIVE_SLOT = 15
GRADEUP_NORMAL, GRADEUP_FIX = 0, 1


def is_exclusive(item_id):
    row = item(item_id)
    return bool(row) and to_int(row.get('itemType'), -1) == EXCLUSIVE_SLOT


def exclusive_recipe(item_id):
    """Everything both grade-up modes need, or None at the top of the chain."""
    row = item(item_id)
    if not row or to_int(row.get('itemType'), -1) != EXCLUSIVE_SLOT:
        return None
    result = to_int(row.get('result_itemID'), -1)
    if result < 0:
        return None
    return {
        'material': to_int(row.get('req_itemID'), -1),
        'count': max(to_int(row.get('req_itemCount'), 0), 0),
        'gold': max(to_int(row.get('gradeUp_gold'), 0), 0),
        'diamond': max(to_int(row.get('gradeUp_Diamond'), 0), 0),
        'extra': (to_int(row.get('ResourceType_1'), -1),
                  to_int(row.get('ResourceType_2'), -1),
                  max(to_int(row.get('ResourceVal_1'), 0), 0)),
        'result': result,
        'ratio': to_int(row.get('SuccessRatio'), 1000),
        'fusion': to_int(row.get('FusionCount'), -1),
    }


def enhance_costs(recipe):
    """[(type1, type2, amount)] an Enhance charges, in the order it checks."""
    out = []
    if recipe['material'] > 0 and recipe['count'] > 0:
        out.append((ResourceType.Item, recipe['material'], recipe['count']))
    t1, t2, val = recipe['extra']
    if t1 >= 0 and val > 0:
        out.append((t1, t2, val))
    if recipe['gold'] > 0:
        out.append((ResourceType.Gold, -1, recipe['gold']))
    if recipe['diamond'] > 0:
        out.append((ResourceType.TotalCash, -1, recipe['diamond']))
    return out


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
