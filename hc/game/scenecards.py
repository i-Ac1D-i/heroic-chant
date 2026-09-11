"""Relics -- which the protocol calls Scene Cards.

**Read this before touching anything named "relic" or "artifact".** The client's
English UI and its own internal names disagree, and getting them the wrong way
round wastes a session:

| UI says   | protocol/tables say | ResourceType | EItemType slots |
|-----------|---------------------|--------------|-----------------|
| **Relic** | SceneCard           | 42           | 9, 10, 11       |
| Artifact  | Artifact            | 8            | 7 (8 unused)    |

Evidence, not inference:

* string 11593 -- *"Relic Craft KIT: A material used in crafting of Relic.
  Relic can be crafted at the Forge."* -- names ResourceType **127**, whose own
  display name is "Craft KIT", and 127 is exactly what `sceneCardCreateCost`
  charges.
* the Forge's own classes are `DimensionHephaiForgeSceneCard*`, and its packets
  are `SceneCardManufactureReq` and friends. String 11449 is "The Forge",
  11428 "Hohoians' Forge", 11426 "Craft Relic!".
* the awakening tree's relic node, `AwakenRewardRelic.UpdateUI` @0x1BC2078,
  calls `NMUnit.GetUnitAwakenBonusReward(unitID, partsInfoID)` and renders the
  result -- and every row of `unitAwakenBonusReward` pays ResourceType 42.
* `sceneCardInfo.itemType` is 9 for all 144 cards, i.e.
  `EItemType.SceneCardSlot1`; the three equip slots are 9, 10 and 11.

So `hc/game/artifacts.py` is the *Artifact* system. This file is Relics.

A relic is per-instance like an artifact -- its own UID, its own level and skill
grade -- and lives in `NGLoginAckLargeData.vecSceneCardInfo`.

### The Forge

Four craft slots (`sceneCardCreateSlot`; slot 1 is free, the rest cost gold or
cash). A craft spends a chosen hero's Memory plus Craft KIT
(`sceneCardCreateCost`, keyed by `CreateLv`), rolls a grade off that row's
`Grade_1..5_Percent`, and produces one relic:

* if `sceneCardCreateUnitBonus` has a row for (that hero, that rolled grade),
  the result is that row's card -- every row is `Fixed_Reward = 1`. This is how
  a hero's own relic is crafted.
* otherwise it is a uniform pick among cards whose `startGrade` matches, minus
  the ones that only exist as fixed or awakening rewards.

**The craft duration is not in the client tables.** Nothing in
`sceneCardCreateCost`, `sceneCardCreateSlot` or any neighbouring table carries a
time, so it was a server-side policy value on the real service and it is one
here: `forge.craft_seconds`, defaulting to 0 (collectable at once). That is the
only number in this file not taken from the game's own data, and it is flagged
here rather than buried.
"""
import random
from datetime import datetime, timedelta

from ..data.tables import TABLES, to_int
from .enums import ResourceType
from ..settings import SETTINGS

# EItemType, @dump.cs:773764.
SLOT_1, SLOT_2, SLOT_3 = 9, 10, 11
SLOTS = (SLOT_1, SLOT_2, SLOT_3)

SCENE_CARD = ResourceType.SceneCard          # 42
CRAFT_KIT = ResourceType.ArtifactMaterial    # 127, "Craft KIT"
MEMORY = ResourceType.UnitPieces             # 16, a hero's own Memory

MAX_GRADE = 5


def card(card_id):
    return TABLES.row('sceneCardInfo', 'itemID', card_id)


def exists(card_id):
    return card(card_id) is not None


def grade_of(card_id):
    row = card(card_id)
    return to_int(row.get('startGrade'), 1) if row else 1


def card_type(card_id):
    """NGSceneCard.CardType -- `sceneCardInfo.itemType`, 9 for every card."""
    row = card(card_id)
    return to_int(row.get('itemType'), SLOT_1) if row else SLOT_1


def max_level(card_id):
    row = card(card_id)
    return to_int(row.get('maxLevel'), 1) if row else 1


def max_skill_grade(card_id):
    row = card(card_id)
    return to_int(row.get('maxSkillLevel'), 1) if row else 1


def _cached(name, build):
    got = globals().get(name)
    if got is None:
        got = globals()[name] = build()
    return got


def fixed_reward_cards():
    """Cards that only exist as a fixed craft reward for one hero."""
    return _cached('_FIXED', lambda: {
        to_int(r['itemID']) for r in TABLES.sql('sceneCardCreateUnitBonus')})


def awaken_reward_cards():
    """Cards that only come out of a hero's awakening tree."""
    return _cached('_AWAKEN', lambda: {
        to_int(r['resourceType2'])
        for r in TABLES.sql('unitAwakenBonusReward')
        if to_int(r['resourceType1']) == SCENE_CARD})


def random_pool(grade):
    """Cards a plain craft of this grade may produce.

    Fixed-reward and awakening-only cards are held out: they are obtainable,
    just not by rolling.  66 of the 108 grade-5 cards remain, so the pool is
    not thin.
    """
    held_back = fixed_reward_cards() | awaken_reward_cards()
    return sorted({to_int(r['itemID']) for r in TABLES.sql('sceneCardInfo')
                   if to_int(r.get('startGrade'), 1) == int(grade)}
                  - held_back)


def unit_bonus(unit_id, grade):
    """The fixed card this hero's Memory produces at this grade, or None."""
    for r in TABLES.sql('sceneCardCreateUnitBonus'):
        if (to_int(r.get('UnitID'), -1) == int(unit_id)
                and to_int(r.get('Grade'), -1) == int(grade)):
            return to_int(r['itemID'])
    return None


# ---------------------------------------------------------------- the forge --
def create_cost(create_lv):
    """One `sceneCardCreateCost` row: what a craft at this level costs."""
    return TABLES.row('sceneCardCreateCost', 'CreateLv', create_lv)


def create_levels():
    return sorted(to_int(r['CreateLv'])
                  for r in TABLES.sql('sceneCardCreateCost'))


def slot_rows():
    return sorted(TABLES.sql('sceneCardCreateSlot'),
                  key=lambda r: to_int(r.get('SlotID'), 0))


def slot_row(slot_index):
    return TABLES.row('sceneCardCreateSlot', 'SlotID', slot_index)


def slot_open_cost(slot_index):
    """(type1, type2, amount) to open a craft slot, or None if it is free."""
    row = slot_row(slot_index)
    if row is None:
        return None
    t1 = to_int(row.get('OpenCost_Type1'), -1)
    val = to_int(row.get('OpenCost_Val1'), 0)
    if t1 < 0 or val <= 0:
        return None
    return t1, to_int(row.get('OpenCost_Type2'), -1), val


def free_slot():
    """The slot that needs no opening -- SlotID 1, whose OpenType is -1."""
    for row in slot_rows():
        if to_int(row.get('OpenType'), -1) < 0:
            return to_int(row.get('SlotID'), 1)
    return 1


def craft_seconds():
    """Server policy, not client data.  0 means collectable immediately."""
    return max(0, int(SETTINGS.get('forge.craft_seconds', 0)))


def roll_grade(create_lv):
    """Pick a grade from the row's own Grade_1..5_Percent weights."""
    row = create_cost(create_lv)
    if row is None:
        return 1
    weights = []
    for g in range(1, MAX_GRADE + 1):
        try:
            weights.append(float(row.get('Grade_%d_Percent' % g) or 0))
        except (TypeError, ValueError):
            weights.append(0.0)
    total = sum(weights)
    if total <= 0:
        return 1
    pick = random.uniform(0, total)
    upto = 0.0
    for g, w in enumerate(weights, start=1):
        upto += w
        if pick <= upto:
            return g
    return MAX_GRADE


def roll_card(create_lv, unit_id):
    """(card id, grade) for one craft.  `unit_id` is whose Memory was spent."""
    grade = roll_grade(create_lv)
    fixed = unit_bonus(unit_id, grade)
    if fixed is not None:
        return fixed, grade
    pool = random_pool(grade)
    if not pool:
        # Every card of that grade is held back.  Step down rather than fail;
        # grade 1 always has an open pool.
        for lower in range(grade - 1, 0, -1):
            pool = random_pool(lower)
            if pool:
                grade = lower
                break
    if not pool:
        return None, grade
    return random.choice(pool), grade


# ------------------------------------------------------------------- owning --
def make(uid, card_id):
    """One owned relic, as it is stored on the player."""
    return {'uid': int(uid), 'id': int(card_id), 'exp': 0,
            'skill': 1, 'equip': 0, 'lock': 0}


def info(r):
    """NGSceneCard for one owned relic."""
    from ..protocol.dto import TYPES
    return TYPES['NGSceneCard'](
        UID=int(r['uid']), ID=int(r['id']), EXP=int(r.get('exp', 0)),
        SkillGrade=int(r.get('skill', 1)),
        EquipUnitUID=int(r.get('equip', 0) or 0),
        CardType=card_type(r['id']),
        LockEnable=int(r.get('lock', 0)))


def infos(player):
    return [info(r) for r in player.scenecards()]


def slot_info(slot_index, pending=None):
    """NGSceneCardSlot -- one forge slot, and whatever is cooking in it.

    `ResultArtifactID` stays 0: the same struct serves the artifact forge,
    which is a separate feature and not implemented.
    """
    from ..protocol.dto import TYPES
    pending = pending or {}
    end = pending.get('end')
    return TYPES['NGSceneCardSlot'](
        SlotIndex=int(slot_index),
        ResultSceneCardID=int(pending.get('card', 0) or 0),
        ResultArtifactID=0,
        tmEndTime=_dt(end) if end else datetime(2000, 1, 1))


def _dt(value):
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime(2000, 1, 1)


def finish_time():
    return datetime.utcnow() + timedelta(seconds=craft_seconds())


def ready(pending):
    """Is the craft in this slot collectable yet?"""
    if not pending or not pending.get('card'):
        return False
    return datetime.utcnow() >= _dt(pending.get('end'))


# ---------------------------------------------------------------- levelling --
def level_row(grade, level):
    for r in TABLES.sql('sceneCardLevelInfo'):
        if (to_int(r.get('grade'), -1) == int(grade)
                and to_int(r.get('level'), -1) == int(level)):
            return r
    return None


def material_exp(r):
    """What feeding this relic to another is worth.

    `sceneCardLevelInfo.materialExp` for its grade at its current level, which
    is the table's own name for exactly this.
    """
    row = level_row(grade_of(r['id']), level_of(r))
    return to_int(row.get('materialExp'), 0) if row else 0


def level_of(r):
    """Current level, derived from EXP against `sceneCardLevelInfo.enchantExp`.

    The client stores EXP, not level, so level is whatever the thresholds say.
    """
    grade = grade_of(r['id'])
    exp = int(r.get('exp', 0))
    cap = max_level(r['id'])
    best = 1
    for row in TABLES.sql('sceneCardLevelInfo'):
        if to_int(row.get('grade'), -1) != grade:
            continue
        lv = to_int(row.get('level'), 0)
        if lv <= cap and exp >= to_int(row.get('enchantExp'), 0):
            best = max(best, lv)
    return best


def sell_price(r):
    row = level_row(grade_of(r['id']), level_of(r))
    return to_int(row.get('SellGold'), 0) if row else 0
