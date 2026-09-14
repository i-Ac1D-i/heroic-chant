"""Achievements: lifetime thresholds, claimed once each for a reward.

NMMissionAchievement.GetAchievementInfo() loads achievementList rows into a
plain MissionInfo, so a claim runs through the same MissionInfo.ClearMission
jump table missions use. Three ClearTypes cover every row here, read off that
jump table directly rather than guessed:

  ClearType 7  -> NMUserInfo.GetResourceLongValue(110)          wallet balance of resource 110
  ClearType 8  -> NMUserInfo.GetCollectionTypeAllValue(9)        CollectionType 9 (GachaOpenCount), summed
  ClearType 19 -> NMUserInfo.GetCollectionValue(GetKey(18))      CollectionType 18 (TagArenaTierUpCount)

As elsewhere in this project, MissionClearType and CollectionType are
different enums and the numbers next to each other above are a coincidence,
not a correspondence.

Resource 110 is never paid out anywhere on this server, so the 155
achievementType-2 rows can never complete yet. CollectionType 18 only moves
by playing Tag Arena, which isn't implemented, so the 31 achievementType-4
rows can't complete either. achievementType-3 (GachaOpenCount) already works,
since gacha pulls are tracked for the Guide Mission.
"""
from ..data.tables import TABLES, to_int
from .enums import CollectionType

RESOURCE_COUNT = 7
GACHA_COUNT = 8
TAG_ARENA_COUNT = 19


def row(achievement_id):
    return TABLES.row('achievementList', 'achievementID', achievement_id, source='json')


def reward(achievement_row):
    group = to_int(achievement_row.get('RewardGroup'), -1)
    out = []
    for r in TABLES.json('achievementReward'):
        if to_int(r.get('RewardGroup'), -1) != group:
            continue
        t1 = to_int(r.get('ResourceType1'), -1)
        val = to_int(r.get('Val1'), 0)
        if t1 < 0 or val <= 0:
            continue
        out.append((t1, to_int(r.get('ResourceType2'), -1),
                    to_int(r.get('ResourceType3'), -1), val))
    return out


def counter(player, achievement_row):
    ct = to_int(achievement_row.get('ClearType'), -1)
    if ct == RESOURCE_COUNT:
        return player.get_resource(110)
    if ct == GACHA_COUNT:
        return sum(v for t1, _t2, _t3, v in player.collection_items()
                  if t1 == CollectionType.GachaOpenCount)
    if ct == TAG_ARENA_COUNT:
        return player.get_collection(CollectionType.TagArenaTierUpCount)
    return None


def complete(player, achievement_row):
    value = counter(player, achievement_row)
    if value is None:
        return False
    return value >= to_int(achievement_row.get('ClearVal_1'), 0)


def claimed(player):
    return player.d.setdefault('achievements_claimed', [])
