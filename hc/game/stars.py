"""Story stars: each stage's three stars, their rewards, and the floor chests.

Everything here is read off the client, because the stars the player sees are
computed on the phone from collections the server sends.

### A stage's stars

`StarReward.InitStar` and the floor box both read one collection per stage:

    NormalDungeonStarCount (29)  keyed (29, dungeonID)   -- Normal (modeID 0)
    HardDungeonStarCount   (30)  keyed (30, dungeonID)   -- Hard   (modeID 13)

whose value is the 3-bit mask of stars earned -- bit 0 is star 1, and so on
(NMContents.GetStarRewardFlag @0x1356D28).  The floor's "x/30" is the popcount
of those masks over the floor's stages (StarFloorRewardBox.InitStarFloorReward
@0x1443C14 -> GetFloorDungeonList + GetStarRewardFlagToCount @0x1356D60).
The collection picked is 29 when the stage's modeType is 0 and 30 otherwise.

`modeID` is a ContentsType: 0 is Dungeon (Normal), 13 HardDungeon.

The server used to key 29 by *floor* and store a star count, so no stage ever
showed a star.  `migrate` moves old saves over.

### Per-star rewards

`starSystemReward` rows with a dungeonID pay once per star (starIndex 1-3).
They go out in DungeonEndAck._vecStarReward when a star is first earned.

### Floor chests

`starSystemReward` rows with dungeonID -1 are the three chests under each
floor (starIndex = the stars needed: 10/20/30 and so on).  Whether a chest is
claimed is collection

    NormalDungeonFloorReward (31) / HardDungeonFloorReward (32)
    keyed (31, floor, starIndex)

holding a flag per story season -- Season0 1, Season1 2, Season1_5 4
(ConvertStorySeasonToStarFloorRewardSeasonFlagType @0x136B8F8, table at
0x3C51118 = [1, 2, 4]) -- tested with Enum.HasFlag.  The chest button sends
GetDungeonStarRewardReq(modeType, storySeason, floor, GoalCount).
"""
from ..data.tables import TABLES, to_int
from .enums import CollectionType

NORMAL, HARD = 0, 13                       # ContentsType.Dungeon / HardDungeon
SEASON_FLAG = {0: 1, 1: 2, 2: 4}           # StarFloorRewardSeasonFlagType


def is_hard(mode):
    return int(mode) != NORMAL


def stage_collection(mode):
    return (CollectionType.HardDungeonStarCount if is_hard(mode)
            else CollectionType.NormalDungeonStarCount)


def chest_collection(mode):
    return (CollectionType.HardDungeonFloorReward if is_hard(mode)
            else CollectionType.NormalDungeonFloorReward)


def popcount3(mask):
    return bin(int(mask) & 7).count('1')


def stage_mask(player, mode, dungeon_id):
    return int(player.get_collection(stage_collection(mode), t2=int(dungeon_id))) & 7


def record_stage(player, dungeon_row, star_flag):
    """OR a clear's stars into the stage's mask.  Returns the bits new to it."""
    mode = to_int(dungeon_row.get('modeID'), NORMAL)
    did = to_int(dungeon_row['dungeon_ID'])
    prev = stage_mask(player, mode, did)
    mask = prev | (int(star_flag) & 7)
    if mask != prev:
        player.set_collection(stage_collection(mode), mask, t2=did)
    return mask & ~prev


def stage_rewards(dungeon_id, bits):
    """[(t1, t2, t3, amount)] for the per-star rewards of the given bits."""
    out = []
    for r in TABLES.json('starSystemReward'):
        if to_int(r.get('dungeonID'), -1) != int(dungeon_id):
            continue
        star = to_int(r.get('starIndex'), 0)
        if 1 <= star <= 3 and bits & (1 << (star - 1)):
            amount = to_int(r.get('rewardVal1'), 0)
            if to_int(r.get('rewardType1'), -1) >= 0 and amount > 0:
                out.append((to_int(r['rewardType1']), to_int(r.get('rewardType2'), -1),
                            -1, amount))
    return out


def unpaid_stage_rewards(player, dungeon_id, bits):
    """Per-star rewards for `bits`, less any already paid -- and marks them paid.

    Older saves were paid all three on first clear; `migrate` records that so
    replaying one of those stages for a better score pays nothing twice.
    """
    paid = player.d.setdefault('star_rewards_paid', {})
    key = str(int(dungeon_id))
    due = int(bits) & ~int(paid.get(key, 0)) & 7
    if not due:
        return []
    paid[key] = int(paid.get(key, 0)) | due
    return stage_rewards(dungeon_id, due)


def floor_dungeons(mode, season, floor):
    return [to_int(r['dungeon_ID']) for r in TABLES.json('dungeonlist')
            if to_int(r.get('modeID'), -1) == int(mode)
            and to_int(r.get('storySeason'), -99) == int(season)
            and to_int(r.get('floor'), -1) == int(floor)]


def floor_stars(player, mode, season, floor):
    return sum(popcount3(stage_mask(player, mode, did))
               for did in floor_dungeons(mode, season, floor))


def chest_rows(mode, season, floor, star_count):
    return [r for r in TABLES.json('starSystemReward')
            if to_int(r.get('dungeonID'), 0) == -1
            and to_int(r.get('modeID'), -1) == int(mode)
            and to_int(r.get('storySeason'), -99) == int(season)
            and to_int(r.get('floor'), -1) == int(floor)
            and to_int(r.get('starIndex'), -1) == int(star_count)]


def chest_reward(rows):
    return [(to_int(r['rewardType1']), to_int(r.get('rewardType2'), -1), -1,
             to_int(r.get('rewardVal1'), 0))
            for r in rows if to_int(r.get('rewardType1'), -1) >= 0
            and to_int(r.get('rewardVal1'), 0) > 0]


def chest_claimed(player, mode, season, floor, star_count):
    flag = SEASON_FLAG.get(int(season))
    if flag is None:
        return True
    value = player.get_collection(chest_collection(mode), t2=int(floor), t3=int(star_count))
    return bool(int(value) & flag)


def mark_chest(player, mode, season, floor, star_count):
    ctype = chest_collection(mode)
    value = int(player.get_collection(ctype, t2=int(floor), t3=int(star_count)))
    player.set_collection(ctype, value | SEASON_FLAG[int(season)],
                          t2=int(floor), t3=int(star_count))


MIGRATION = 1


def migrate(player):
    """Move a save from the old star records to the ones the client reads.

    The old code wrote collection 29 keyed by *floor*, holding a star count.
    Read as (29, dungeonID) those turn into phantom stars on stages 1-20, so
    every old 29/30 entry is dropped and rebuilt from `cleared` -- the per-stage
    star masks the server has always kept.  The old code also paid all three
    per-star rewards on a stage's first clear; that is recorded so they are
    not paid again.  Runs once per save.
    """
    if int(player.d.get('stars_migration', 0)) >= MIGRATION:
        return False
    cols = player.d.setdefault('collections', {})
    for key in list(cols):
        if to_int(key.split(':')[0], -1) in (CollectionType.NormalDungeonStarCount,
                                               CollectionType.HardDungeonStarCount):
            del cols[key]
    paid = player.d.setdefault('star_rewards_paid', {})
    index = TABLES.index('dungeonlist', 'dungeon_ID')
    for did, mask in (player.d.get('cleared') or {}).items():
        row = index.get(to_int(did))
        if row is None:
            continue
        mode = to_int(row.get('modeID'), -1)
        if mode not in (NORMAL, HARD):
            continue
        if int(mask) & 7:
            player.set_collection(stage_collection(mode), int(mask) & 7, t2=to_int(did))
        paid[str(to_int(did))] = 7
    player.d['stars_migration'] = MIGRATION
    return True
