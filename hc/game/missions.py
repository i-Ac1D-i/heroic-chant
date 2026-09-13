"""Missions: progress, the daily and weekly reset, and claiming rewards.

The mission claim used to be a stub.  `GetMissionRewardReq` marked the mission
done and paid out nothing, and no fight ever advanced a mission counter -- so
the arena's daily reward did nothing at all when claimed.

### How the client scores a mission (read off the client, not guessed)

`NMMission.CheckClearMission(NGMissionInfo)` @0x1F383CC looks the mission up in
`missionList` and calls `MissionInfo.ClearMission(StartCollectionValue, ...)`
@0x2278844, which switches on `ClearType` (a jump table at 0x3C745B4, indexed
by ClearType - 1) and compares a collection counter against `ClearVal_1`.

**`MissionClearType` and `CollectionType` are different enums**, and the
numbers do not line up.  The two cases implemented here:

| MissionClearType         | reads CollectionType   | key                         |
|--------------------------|------------------------|-----------------------------|
| 12 ContentsPlayCount     | 13 ContentsPlayCount   | GetKey(13, ClearVal_2)      |
| 13 ContentsClearCount    | 14 ContentsClearCount  | GetKey(14, ClearVal_2)      |

Case 12 (@0x2278B78) loads 13 and ClearVal_2 into `Type3int64.GetKey(a, b)`,
so a "play the arena" mission reads the play counter for contents 6
(`ContentsType.ArenaAttack`), not a counter numbered 12.  Get that the wrong
way round and the mission never moves.

Progress is the counter minus `StartCollectionValue`, which is how a daily
mission restarts from zero each day without the counter itself being reset.

### What is inferred

* `Season` is used here as the period index -- the day for daily missions, the
  ISO week for weekly ones -- so that a new period gives a fresh
  StartCollectionValue and a cleared `IsReceived`.  The client only ever sees
  the baseline and the received flag, so the exact number does not matter as
  long as it changes per period.  INFERRED.
* Only clear types 12 and 13 are validated on the server.  Any other claim is
  trusted, because the client only enables Claim when its own ClearMission
  check has passed, and the old handler accepted everything anyway.  The
  per-period received flag still stops a reward being taken twice.
"""
from datetime import datetime

from ..data.tables import TABLES, to_int
from .enums import CollectionType

DAILY, WEEKLY = 1, 2              # MissionType @dump.cs:642083
GUIDE = 21                        # MissionType.GuideMission

# MissionClearType -> (CollectionType it reads, which ClearVal is the key's
# second part, which ClearVal is the target).  Every row decoded from the
# ClearMission jump table at 0x3C745B4, with the comparison read from the two
# shared tails: @0x2279394 (single key, `value - start >= ClearVal_1`) and
# @0x22794C0 (two-part key, `value - start >= ClearVal_2`).
#
#   12 ContentsPlayCount  @0x2278B78  GetKey(13, ClearVal_2)  >= ClearVal_1
#   13 ContentsClearCount             GetKey(14, ClearVal_2)  >= ClearVal_1
#   31 AttendanceCount    @0x2278FFC  GetKey(34)              >= ClearVal_1
#   34 UnitLevelUp        @0x2278BB4  GetKey(37, ClearVal_1)  >= ClearVal_2
KEY_NONE, KEY_VAL1, KEY_VAL2 = None, 'ClearVal_1', 'ClearVal_2'
CLEAR_RULES = {
    12: (CollectionType.ContentsPlayCount, KEY_VAL2, 'ClearVal_1'),
    13: (CollectionType.ContentsClearCount, KEY_VAL2, 'ClearVal_1'),
    31: (CollectionType.AttendanceCount, KEY_NONE, 'ClearVal_1'),
    34: (CollectionType.UnitLevelUp, KEY_VAL1, 'ClearVal_2'),
}
# Kept for callers that only care about the counter type.
CLEAR_TO_COLLECTION = {k: v[0] for k, v in CLEAR_RULES.items()}

# ContentsType.ArenaAttack -- the contents id arena missions are keyed on.
ARENA_CONTENTS = 6


def row(mission_id):
    return TABLES.row('missionList', 'missionID', mission_id, source='json')


def reward_rows(group):
    return [r for r in TABLES.json('missionReward')
            if to_int(r.get('RewardGroup'), -1) == int(group)]


def reward(mission_row):
    """[(t1, t2, t3, amount)] a mission pays, from `missionReward`."""
    out = []
    for r in reward_rows(to_int(mission_row.get('RewardGroup'), -1)):
        t1 = to_int(r.get('ResourceType1'), -1)
        amount = to_int(r.get('Val1'), 0)
        if t1 < 0 or amount <= 0:
            continue
        out.append((t1, to_int(r.get('ResourceType2'), -1),
                    to_int(r.get('ResourceType3'), -1), amount))
    return out


def period(mission_type, now=None):
    """The Season a mission of this type belongs to right now.  INFERRED."""
    now = now or datetime.utcnow()
    if int(mission_type) == DAILY:
        return now.date().toordinal()
    if int(mission_type) == WEEKLY:
        year, week, _ = now.isocalendar()
        return year * 100 + week
    return 0


def periodic():
    """The daily and weekly missions -- the ones that reset and go out at login."""
    return [r for r in TABLES.json('missionList')
            if to_int(r.get('missionType'), -1) in (DAILY, WEEKLY)]


def counter(player, mission_row):
    """The collection value this mission reads, or None if not implemented."""
    rule = CLEAR_RULES.get(to_int(mission_row.get('ClearType'), -1))
    if rule is None:
        return None
    ctype, key_from, _target = rule
    t2 = to_int(mission_row.get(key_from), -1) if key_from else -1
    return int(player.get_collection(ctype, t2=t2))


def target(mission_row):
    rule = CLEAR_RULES.get(to_int(mission_row.get('ClearType'), -1))
    column = rule[2] if rule else 'ClearVal_1'
    return to_int(mission_row.get(column), 0)


def periodic_type(mission_type):
    """Daily and weekly missions restart each period; everything else -- the
    Guide Mission among them -- counts a lifetime total from zero."""
    return int(mission_type) in (DAILY, WEEKLY)


def state(player, mission_row, now=None):
    """This mission's stored state, rolled over to the current period."""
    mid = str(to_int(mission_row['missionID']))
    book = player.d.setdefault('missions', {})
    season = period(to_int(mission_row.get('missionType'), 0), now)
    entry = book.get(mid)
    if entry is None or int(entry.get('season', -1)) != season:
        # A daily or weekly mission starts from wherever the counter stands
        # now.  A lifetime mission starts from zero: "clear stage 1-3" must
        # count a stage the player cleared last month.
        start = counter(player, mission_row)             if periodic_type(mission_row.get('missionType', 0)) else 0
        entry = book[mid] = {
            'season': season,
            'start': int(start) if start is not None else 0,
            'received': False,
            'since': (now or datetime.utcnow()).isoformat(timespec='seconds'),
        }
    return entry


def progress(player, mission_row, now=None):
    """(done, needed), or (None, needed) when the type is not implemented."""
    need = target(mission_row)
    value = counter(player, mission_row)
    if value is None:
        return None, need
    return value - int(state(player, mission_row, now)['start']), need


def complete(player, mission_row, now=None):
    done, need = progress(player, mission_row, now)
    if done is None:
        return True                      # trusted -- see the module docstring
    return done >= need


def info(player, mission_row, now=None):
    from ..protocol.dto import TYPES
    from . import state as game_state
    entry = state(player, mission_row, now)
    return TYPES['NGMissionInfo'](
        Season=int(entry['season']), ID=to_int(mission_row['missionID']),
        Type=to_int(mission_row.get('missionType'), 0),
        StartCollectionValue=int(entry['start']),
        IsReceived=bool(entry['received']),
        tmRegDate=game_state._dt(entry.get('since')))


def guide():
    """The 147 Guide Mission missions -- MissionType 21, every one of them."""
    return [r for r in TABLES.json('missionList')
            if to_int(r.get('missionType'), -1) == GUIDE]


def infos(player, now=None):
    """Mission state sent at login: daily, weekly and the Guide Mission.

    The guide list needs an entry per mission: the chapter screen calls
    NMUserInfo.GetMission for each and NMResource.CheckCompleteLastMission
    @0x19FFD24 treats a missing one as not done.
    """
    return [info(player, r, now) for r in periodic() + guide()]


def touch(player, now=None):
    """Create this period's state for every mission that goes out at login.

    Done before any counter moves and before the login save, so a baseline is
    pinned to the pre-login value and actually persisted -- building the state
    lazily inside the login payload, after the save, lost it.
    """
    for r in periodic() + guide():
        state(player, r, now)


def record_login(player, now=None):
    """AttendanceCount: +1 per UTC day the account logs in."""
    today = (now or datetime.utcnow()).date().isoformat()
    if player.d.get('attendance_day') != today:
        player.d['attendance_day'] = today
        player.add_collection(CollectionType.AttendanceCount, 1)


def record_unit_level(player, unit):
    """UnitLevelUp: the highest level any copy of this hero has reached."""
    uid = int(unit['id'])
    level = int(unit.get('level', 1))
    if level > player.get_collection(CollectionType.UnitLevelUp, t2=uid):
        player.set_collection(CollectionType.UnitLevelUp, level, t2=uid)


# --------------------------------------------------------------------------
# More counters the Guide Mission reads.  Each key and comparison is decoded
# from MissionInfo.ClearMission; the client does the scoring itself from these,
# so the job here is only to keep the counter right.
#
#   10 DimensionGachaOpenCount  @0x2278F18  GetCollectionTypeAllValue(9)
#                               -- the sum over every banner -- >= ClearVal_1
#   15 SceneCardGradeGetCount   @0x2278F54  GetKey(15, grade), or the sum of
#                               type 15 when ClearVal_2 is -1   >= ClearVal_1
#   29 TargetUnitGradeUp...     @0x22788FC  GetKey(6, unitID, grade) > 0
#   46 TotalUnitLevelUp         @0x22790D0  GetCollectionTypeAllValue(37)
#                               >= ClearVal_1 -- no baseline subtracted
#   56 AwakenPartsSlotIDMission @0x22794B0  GetKey(43, partsID), or the sum
#                               of type 43 when ClearVal_1 is -1 >= ClearVal_2
#
# 64 UnitEquipAtSpecificSlot and 65 UnitSpecificAwaken read no counter at all:
# they call into NMUserInfo and check the hero's own equipment and awakening,
# which already go out in NGUnitInfo.
#
# INFERRED, and flagged: a multi-pull counts one open per hero summoned; key
# 43's second part is the awakening node id (the mission name is
# "AwakenPartsSlotID", and the guide rows only ever use the any-node sum, so
# the choice cannot change a guide result); and a Relic counts towards type 15
# however it was obtained, because the counter is named "Get" count, not craft
# count.

def record_summon(player, gacha_id, count=1):
    player.add_collection(CollectionType.GachaOpenCount, int(count),
                          t2=int(gacha_id))


def record_unit_grade(player, unit):
    """Mark every grade this hero has reached, up to its current one."""
    uid, grade = int(unit['id']), int(unit.get('grade', 0))
    for g in range(0, grade + 1):
        if player.get_collection(CollectionType.GetUnitCount, t2=uid, t3=g) <= 0:
            player.set_collection(CollectionType.GetUnitCount, 1, t2=uid, t3=g)


def record_awaken_node(player, parts_id):
    player.add_collection(CollectionType.AwakenPartsSlotOpenCount, 1,
                          t2=int(parts_id))


def record_scenecard(player, card_id):
    from . import scenecards
    player.add_collection(CollectionType.SceneCardGradeGetCount, 1,
                          t2=scenecards.grade_of(card_id))


def _raise_to(player, ctype, value, t2=-1, t3=-1):
    """Backfill never lowers a counter -- they only ever go up in the game."""
    if int(value) > player.get_collection(ctype, t2=t2, t3=t3):
        player.set_collection(ctype, int(value), t2=t2, t3=t3)


def backfill(player):
    """Counters the server only started keeping now, rebuilt from the save so
    progress made before this change still counts.  Idempotent."""
    from . import scenecards
    nodes, grades = {}, {}
    for unit in player.d.get('units', []):
        record_unit_level(player, unit)
        record_unit_grade(player, unit)
        for node in (unit.get('awaken') or []):
            nodes[int(node)] = nodes.get(int(node), 0) + 1
    for node, n in nodes.items():
        _raise_to(player, CollectionType.AwakenPartsSlotOpenCount, n, t2=node)
    for relic in player.d.get('scenecards', []):
        g = scenecards.grade_of(relic['id'])
        grades[g] = grades.get(g, 0) + 1
    for g, n in grades.items():
        _raise_to(player, CollectionType.SceneCardGradeGetCount, n, t2=g)
    for gid, rec in (player.d.get('summons') or {}).items():
        try:
            _raise_to(player, CollectionType.GachaOpenCount,
                      int(rec.get('count', 0)), t2=int(gid))
        except (TypeError, ValueError):
            continue


def record_arena_fight(player, won):
    """Advance the arena's mission counters for one fight.

    The period baselines are pinned *before* the counter moves.  A baseline is
    created lazily, so without this a server restart mid-day followed by a
    fight would set today's start to the post-fight value and that fight would
    not count.
    """
    for r in periodic():
        state(player, r)
    player.add_collection(CollectionType.ContentsPlayCount, 1, t2=ARENA_CONTENTS)
    if won:
        player.add_collection(CollectionType.ContentsClearCount, 1,
                              t2=ARENA_CONTENTS)
