"""The Advent Boss (the client's BossDungeon / personal boss).

Read off the client:

* ``personalBossInfo`` (json) holds the floors: groups 1-3 of 17 (90001..,
  91001.., 92001..) and the newer group 4 (93010..93017), each with its
  stamina cost (CostType1 129 TotalDungeonTicket, Costvalue).  Group 4 also
  costs a Whale Boss ticket (``NewBossDungeonInfo``: 181 x1).  Floors chain
  through ``dungeonlist.preDungeonID``; modeID 16.  Drops are the ordinary
  stage tables (``rewards.roll``).
* ``BossDungeonTeamSelectSceneInit.InGameStart`` sends StartBossDungeonReq,
  whose Ack carries NGCheckServerInfo -- so the stamina is charged here.
* ``BossDungeonPlayInit.GameEnd`` @0x1C993EC reports every battle, won or
  lost (ClearType 1 is a win): group 4 with EndNewBossDungeonReq (30344, plus
  a team snapshot), the rest with EndBossDungeonReq (30180).  Both are
  answered with EndBossDungeonAck (40196); there is no other.
* The daily bonus: ``dailyRewardGroup`` 16 is "3 a day" (seasonType 1, the
  Daily season) and ``dailyReward`` lists each floor's bonus (Boss Artifact
  Material, 131).  ``EndBossDungeonAck._vecDailyReward`` carries what was
  paid; the count goes back as ``NGCheckServerInfo
  .vecChangeBossDungeonDailyReward`` (and ``NGLogInAck01
  .vecBossDungeonDailyReward`` at login, which the Advent Boss screen indexes
  without a check).

INFERRED: the daily bonus is paid on the first three wins of the day, one
count each; losses cost the stamina and pay nothing.  ``_gainTicketCount`` is
left at 0.
"""
from ..data.tables import TABLES, to_int
from ..protocol.dto import TYPES
from .enums import CollectionType
from . import state

WIN = 1
DAILY_GROUP = 16              # dailyRewardGroup / dailyReward groupID for the Advent Boss
NEED_PREVIOUS = -723          # "This event is not open at the moment." -- what it answered before


def row(dungeon_id):
    return TABLES.row('personalBossInfo', 'dungeonID', int(dungeon_id), source='json')


def cost(dungeon_id):
    """[(type1, type2, amount)] to enter a floor."""
    r = row(dungeon_id)
    if r is None:
        return []
    out = [(to_int(r['CostType1']), to_int(r.get('CostType2'), -1), to_int(r['Costvalue'], 0))]
    extra = TABLES.row('NewBossDungeonInfo', 'dungeonID', int(dungeon_id), source='json')
    if extra:
        out.append((to_int(extra['addCostType1']), to_int(extra.get('addCostType2'), -1),
                    to_int(extra['addCostValue'], 0)))
    return [c for c in out if c[2] > 0]


def cleared(player, dungeon_id):
    return player.get_collection(CollectionType.DungeonClearCount, t2=int(dungeon_id)) > 0


def can_start(player, dungeon_id):
    from .errors import Err
    if row(dungeon_id) is None:
        return Err.NOT_FOUND
    pre = to_int((TABLES.dungeon(int(dungeon_id)) or {}).get('preDungeonID'), -1)
    if pre > 0 and not cleared(player, pre):
        return NEED_PREVIOUS
    for t1, t2, n in cost(dungeon_id):
        have = player.total_cash() if t1 == player.TOTAL_CASH else player.get_resource(t1, t2)
        if have < n:
            return Err.NOT_ENOUGH
    return 0


def charge(player, dungeon_id):
    for t1, t2, n in cost(dungeon_id):
        player.spend_resource(t1, n, t2)


def record_win(player, dungeon_id):
    """Count the clear; True the first time."""
    first = not cleared(player, dungeon_id)
    player.add_collection(CollectionType.DungeonClearCount, 1, t2=int(dungeon_id))
    player.mark_cleared(int(dungeon_id))
    return first


# -- the daily bonus -------------------------------------------------------
def _group_row(group):
    return TABLES.row('dailyRewardGroup', 'groupID', int(group), source='json')


def daily_count(player, group, now=None):
    """Today's count for a dailyRewardGroup.  Old saves stored a bare number
    with no day; that reads as not today."""
    r = _group_row(group)
    season = state.season_value(to_int(r.get('seasonType'), 1) if r else 1, now)
    v = player.d.get('boss_daily_reward', {}).get(str(int(group)))
    if isinstance(v, list) and len(v) == 2 and int(v[0]) == season:
        return int(v[1])
    return 0


def daily_info(player, group, now=None):
    r = _group_row(group)
    season = state.season_value(to_int(r.get('seasonType'), 1) if r else 1, now)
    return TYPES['NGBossDungeonDailyReward'](GropuID=int(group),
                                             RewardCount=daily_count(player, group, now),
                                             Season=int(season))


def daily_infos(player, now=None):
    return [daily_info(player, to_int(r['groupID']), now) for r in TABLES.json('dailyRewardGroup')]


def take_daily(player, dungeon_id, now=None):
    """The floor's daily bonus if today's count has room; returns the
    rewards (maybe empty)."""
    r = _group_row(DAILY_GROUP)
    limit = to_int(r.get('rewardCount'), 0) if r else 0
    paid = [(to_int(x['rewardType1']), to_int(x['rewardType2']), -1, to_int(x['rewardvalue'], 0))
            for x in TABLES.rows('dailyReward', 'dungeonID', int(dungeon_id), source='json')
            if to_int(x['groupID']) == DAILY_GROUP]
    n = daily_count(player, DAILY_GROUP, now)
    if not paid or n >= limit:
        return []
    season = state.season_value(to_int(r.get('seasonType'), 1), now)
    player.d.setdefault('boss_daily_reward', {})[str(DAILY_GROUP)] = [season, n + 1]
    return paid
