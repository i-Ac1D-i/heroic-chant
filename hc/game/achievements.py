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
