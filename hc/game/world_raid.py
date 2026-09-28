"""World Raid, single play (the solo tab of the World Raid screen).

Read off the client:

* ``worldRaidDungeonList`` (json): four groups, each with a 1-player raid
  (120101..120104) and a 2-player one (120201..120204).  Each names its
  ``preDungeonID``, a clear kill count (10), a join cost (gold, 2-player
  only), a clear cost (one World Raid ticket, ResourceType 157, 5 a day from
  ResourceRefresh) and a ``rewardID``.
* ``worldRaidDungeonReward``: per rewardID, reward tiers by kill count (10,
  18, 26, 35).  They are cumulative -- a raid's ``rewardClear`` preview is
  exactly the four tiers added up.
* Unlocks: ``NMUserInfo.CheckWorldRaidSingleDungeonOpen`` @0x13A150C opens a
  raid when it has no preDungeonID, or when collection WorldRaidMaxKillCount
  (48) keyed by the previous raid's *group* is at least that raid's
  ``clear_kill_count``.  (``worldRaidOpenGroup`` is server-only data; the
  client never loads it.)
* ``WorldRaidSingleTeamSelectSceneInit.InGameStart`` checks no cost before
  WorldRaidGameStartReq; ``WorldSoloRaidScene.GameEnd`` sends
  WorldRaidGameEndReq with ``_MonsterKillCount`` (GetEnemyDieCount).  The
  End Ack's rewards are shown from the NGCheckServerInfo diff.
* The daily bonus: ``dailyRewardGroup`` 2001 (World Raid, 3 a day) and each
  raid's ``dailyReward`` rows; counts go out as NGWorldRaidDailyReward at
  login and in ``vecChangeWorldRaidDailyReward``.  errorString 1200 is "You
  don't have enough Dungeon Tickets.", 1207 "This World Raid is not open
  yet.", 1372 "You don't have enough Gold to join World Raid."

INFERRED: a run that reaches the clear kill count spends the clear ticket
and pays every tier reached plus the daily bonus (first three a day); a
shorter run pays nothing and costs nothing.  The ticket is checked at the
start so a run cannot end unpaid.  ``vecWorldRaidClearInfo`` is sent as
(group, best kill count) pairs.
"""
from ..data.tables import TABLES, to_int
from ..protocol.dto import TYPES
from . import boss

MAX_KILLS = 48                   # CollectionType.WorldRaidMaxKillCount, keyed by group
DAILY_GROUP = 2001               # dailyRewardGroup for the World Raid
NO_TICKET = 1200
NOT_OPEN = 1207
NO_JOIN_GOLD = 1372


def row(dungeon_id):
    return TABLES.row('worldRaidDungeonList', 'dungeon_ID', int(dungeon_id), source='json')


def rows():
    return TABLES.json('worldRaidDungeonList')


def best_kills(player, group):
    return player.get_collection(MAX_KILLS, t2=int(group))


def is_open(player, dungeon_id):
    r = row(dungeon_id)
    if r is None:
        return False
    pre = row(to_int(r.get('preDungeonID'), -1))
    if pre is None:
        return True
    return best_kills(player, to_int(pre['groupID'])) >= to_int(pre['clear_kill_count'], 0)


def join_cost(r):
    return (to_int(r['join_cost_Type1']), to_int(r.get('join_cost_Type2'), -1),
            to_int(r['join_cost_Val1'], 0))


def clear_cost(r):
    return (to_int(r['clear_cost_Type1']), to_int(r.get('clear_cost_Type2'), -1),
            to_int(r['clear_cost_Val1'], 0))


def can_start(player, dungeon_id):
    from .errors import Err
    r = row(dungeon_id)
    if r is None:
        return Err.NOT_FOUND
    if not is_open(player, dungeon_id):
        return NOT_OPEN
    t1, t2, n = clear_cost(r)
    if n > 0 and player.get_resource(t1, t2) < n:
        return NO_TICKET
    t1, t2, n = join_cost(r)
    if n > 0 and (player.total_cash() if t1 == player.TOTAL_CASH
                  else player.get_resource(t1, t2)) < n:
        return NO_JOIN_GOLD
    return 0


def charge_join(player, dungeon_id):
    t1, t2, n = join_cost(row(dungeon_id))
    if n > 0:
        player.spend_resource(t1, n, t2)


def tier_rewards(dungeon_id, kills):
    """Every reward tier the kill count reached, added together."""
    r = row(dungeon_id)
    rid = to_int(r['rewardID'])
    out = {}
    for x in TABLES.rows('worldRaidDungeonReward', 'rewardID', rid, source='json'):
        if to_int(x['monsterKillCount'], 0) <= int(kills):
            key = (to_int(x['resource_type1']), to_int(x['resource_type2']),
                   to_int(x['resource_type3']))
            out[key] = out.get(key, 0) + to_int(x['val_1'], 0)
    return [(k[0], k[1], k[2], v) for k, v in out.items() if v > 0]


def finish(player, dungeon_id, kills, now=None):
    """Record a run.  Returns (rewards, daily_rewards); both empty when the
    run fell short of the clear kill count."""
    r = row(dungeon_id)
    group = to_int(r['groupID'])
    kills = max(int(kills), 0)
    if kills > best_kills(player, group):
        player.set_collection(MAX_KILLS, kills, t2=group)
    if kills < to_int(r['clear_kill_count'], 0):
        return [], []
    t1, t2, n = clear_cost(r)
    if n > 0 and not player.spend_resource(t1, n, t2):
        return [], []
    return tier_rewards(dungeon_id, kills), boss.take_daily(player, dungeon_id, now,
                                                            group=DAILY_GROUP)


def clear_infos(player):
    """NGLogInAck01 / End Ack vecWorldRaidClearInfo: (group, best kills)."""
    groups = sorted({to_int(r['groupID']) for r in rows()})
    return [TYPES['NGPairInt2'](first=g, second=int(best_kills(player, g)))
            for g in groups if best_kills(player, g) > 0]


def daily_info(player, now=None):
    b = boss.daily_info(player, DAILY_GROUP, now)
    return TYPES['NGWorldRaidDailyReward'](GropuID=b.GropuID, RewardCount=b.RewardCount,
                                           Season=b.Season)
