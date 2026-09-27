"""The Trial Tower (the client's OrdealTower / Ordeal dungeon).

Read off the client:

* ``TowerDungeonInfo`` (json) holds the floors.  Group -1 is the main tower,
  180 floors (dungeon 80001..80180); groups 0..4 are 80-floor towers
  (82xxx..86xxx) and group 6 a 10-floor one (87xxx).  Each floor names a
  ``clearRewardGroup`` and, on the main tower, a ``waitRewardGroup`` (the
  "Standby" reward) in ``TowerDungeonRewardGroup``.  ``dungeonlist`` chains
  them with preDungeonID; modeID 14.
* Progress is the ordinary DungeonClearCount (collection 0) per floor.  A
  floor's clear reward is *claimed*, separately, and counted in
  DungeonGetRewardCount (collection 58): ``OrdealDungeonStageSelectSceneInit
  .SetPlayAbleDungeon`` @0x18BFB58 keeps the player on the floor below while
  its reward count is behind its clear count, so the next floor opens only
  once the reward is taken (``GetOrdealTowerClearRewardReq``, from the floor
  list's reward button or the Clear button).
* ``EndOrdealTowerReq`` is sent after every battle, won or lost
  (``OrdealDungeonPlayScene.GameEnd`` @0x18BC0F4); ClearType 1 is a win.  Its
  Ack carries the play counts.
* There is no entry ticket: ``OrdealDungeonTeamSelectSceneInit.InGameStart``
  checks no cost.  The side towers have a daily limit instead,
  ``NMResource.GetOrdealTowerGroupDailyLimit`` -- ConstValue
  ORDEALTOWER_GROUPID_<n>: 3 a day for 0..3 and 6, none (-1) for the main
  tower and 4 -- counted by ``NGOrdealTowerPlayCount`` (GroupID,
  DailySeason, PlayCount), which the client matches against today's Daily
  season value.  errorString 1354 is the "used up all your daily entrance
  chances" message.
* The shop (``NGOrdealTowerShop`` in NGLogInAck01, the Buy Ack and the
  ShopInfo Ack) is a list of GoodsUIDs with a bought count, open until
  tmOpenSeasonEnd -- ``ShopFormUI.Init`` counts down to it and shows the
  player's main-tower floor next to ``NextOrdealShopStageIndex``, the next
  ``TowerShopGoodsInfo.openFloor``.  Goods are ``TowerShopGoodsGroup`` rows.

INFERRED -- the retail rules lived on the server:

* Plays are counted when the battle ends, won or lost.
* The Standby reward is the waitRewardGroup of the highest main-tower floor
  cleared, once a Daily season.
* The shop runs a calendar week.  Its stock is rolled from the
  ``TowerShopGoodsInfo`` tier the player has reached (the highest openFloor at
  or below their main-tower floor): the groupType-0 group always, then other
  groups by ``frequency`` until ``goodsCount`` are listed, one good from each
  by the good's own frequency.  Reaching a new tier re-rolls it.
"""
import random

from ..data.tables import TABLES, to_int
from ..protocol.dto import TYPES
from .enums import CollectionType
from . import state

MAIN = -1
WIN = 1                       # EndOrdealTowerReq._ClearType of a won battle
REWARD_COUNT = 58             # CollectionType.DungeonGetRewardCount

# errorString ids
LIMIT = 1354                  # "You have used up all your daily entrance chances..."
ALREADY_CLEARED = 1158        # "This floor has already been cleared."
NEED_PREVIOUS = 1157          # "This floor is unavailable at the moment."
NOT_CURRENT = 1162            # "You can only receive Standby rewards from This current stage..."
SHOP_OVER = 2411              # "The time limit for purchasing ... has passed."


# -- floors ----------------------------------------------------------------
def row(dungeon_id):
    return TABLES.row('TowerDungeonInfo', 'dungeonID', int(dungeon_id), source='json')


def group_of(dungeon_id):
    r = row(dungeon_id)
    return to_int(r['groupID']) if r else None


def floors(group):
    return sorted(TABLES.rows('TowerDungeonInfo', 'groupID', int(group), source='json'),
                  key=lambda r: to_int(r['floor']))


def previous(dungeon_id):
    d = TABLES.dungeon(int(dungeon_id)) or {}
    return to_int(d.get('preDungeonID'), -1)


def clears(player, dungeon_id):
    return player.get_collection(CollectionType.DungeonClearCount, t2=int(dungeon_id))


def claims(player, dungeon_id):
    return player.get_collection(REWARD_COUNT, t2=int(dungeon_id))


def highest_cleared(player, group=MAIN):
    """The highest floor number cleared in a tower (0 for none)."""
    best = 0
    for r in floors(group):
        if clears(player, r['dungeonID']) > 0:
            best = max(best, to_int(r['floor']))
    return best


def reward_rows(group_id):
    return [(to_int(r['resourceType1']), to_int(r['resourceType2']), -1,
             to_int(r['resourceValue'], 0))
            for r in TABLES.rows('TowerDungeonRewardGroup', 'rewardGroup', int(group_id),
                                 source='json')]


# -- daily plays -----------------------------------------------------------
def daily_limit(group):
    c = TABLES.json('ConstValue')[0]
    key = 'ORDEALTOWER_GROUPID_DEFAULT' if int(group) < 0 else 'ORDEALTOWER_GROUPID_%d' % int(group)
    return to_int(c.get(key), -1)


def _plays(player):
    return player.d.setdefault('ordeal_plays', {})


def play_count(player, group, now=None):
    season, n = _plays(player).get(str(int(group)), (None, 0))
    return int(n) if season == state.season_value(state.SEASON_DAILY, now) else 0


def add_play(player, group, now=None):
    today = state.season_value(state.SEASON_DAILY, now)
    _plays(player)[str(int(group))] = [today, play_count(player, group, now) + 1]


def play_count_infos(player, now=None):
    today = state.season_value(state.SEASON_DAILY, now)
    return [TYPES['NGOrdealTowerPlayCount'](GroupID=int(g), DailySeason=int(season),
                                            PlayCount=int(n))
            for g, (season, n) in sorted(_plays(player).items(), key=lambda x: int(x[0]))
            if season == today]


# -- rules -----------------------------------------------------------------
def can_start(player, dungeon_id, now=None):
    """0, or the errorString id to refuse with."""
    from .errors import Err
    r = row(dungeon_id)
    if r is None:
        return Err.NOT_FOUND
    if clears(player, dungeon_id) > 0:
        return ALREADY_CLEARED
    pre = previous(dungeon_id)
    if pre > 0 and clears(player, pre) <= 0:
        return NEED_PREVIOUS
    group = to_int(r['groupID'])
    limit = daily_limit(group)
    if 0 <= limit <= play_count(player, group, now):
        return LIMIT
    return 0


def record_win(player, dungeon_id):
    if clears(player, dungeon_id) <= 0:
        player.add_collection(CollectionType.DungeonClearCount, 1, t2=int(dungeon_id))


def claim(player, dungeon_id):
    """The floor's clear reward, if it is cleared and not yet claimed; returns
    the rewards paid or None."""
    r = row(dungeon_id)
    if r is None or claims(player, dungeon_id) >= clears(player, dungeon_id):
        return None
    player.add_collection(REWARD_COUNT, 1, t2=int(dungeon_id))
    return reward_rows(to_int(r['clearRewardGroup']))


def wait_reward(player, now=None):
    """The Standby reward of the highest main-tower floor cleared, once a
    day.  Returns (rewards, 0) or (None, error)."""
    top = highest_cleared(player, MAIN)
    r = next((f for f in floors(MAIN) if to_int(f['floor']) == top), None)
    group = to_int(r.get('waitRewardGroup'), -1) if r else -1
    if group < 0:
        return None, NOT_CURRENT
    today = state.season_value(state.SEASON_DAILY, now)
    if player.d.get('ordeal_wait') == today:
        return None, NOT_CURRENT
    player.d['ordeal_wait'] = today
    return reward_rows(group), 0


# -- shop ------------------------------------------------------------------
def shop_tier(player):
    reached = max(highest_cleared(player, MAIN), 1)
    tiers = sorted({to_int(r['openFloor']) for r in TABLES.json('TowerShopGoodsInfo')})
    return max([t for t in tiers if t <= reached] or [tiers[0]])


def goods_row(goods_uid):
    return TABLES.row('TowerShopGoodsGroup', 'goodsUID', int(goods_uid), source='json')


def roll_stock(tier, rng=random):
    rows = [r for r in TABLES.json('TowerShopGoodsInfo') if to_int(r['openFloor']) == tier]
    if not rows:
        return []
    count = max(to_int(r['goodsCount'], 0) for r in rows)
    fixed = [r for r in rows if to_int(r['groupType']) == 0]
    pool = [r for r in rows if to_int(r['groupType']) != 0 and to_int(r['frequency'], 0) > 0]
    picked = list(fixed)
    while pool and len(picked) < count:
        g = rng.choices(pool, weights=[to_int(r['frequency'], 0) for r in pool], k=1)[0]
        pool.remove(g)
        picked.append(g)
    stock = []
    for g in picked:
        goods = [r for r in TABLES.rows('TowerShopGoodsGroup', 'shopGoodsGroup',
                                        to_int(g['shopGoodsGroup']), source='json')
                 if to_int(r['frequency'], 0) > 0]
        if goods:
            pick = rng.choices(goods, weights=[to_int(r['frequency'], 0) for r in goods], k=1)[0]
            stock.append(to_int(pick['goodsUID']))
    return stock


def shop(player, now=None):
    """The player's shop for this week and tier, rolled if it is stale.  The
    roll is seeded by account, week and tier, so an unsaved roll comes out
    the same next time."""
    week = state.season_value(state.SEASON_WEEKLY, now)
    tier = shop_tier(player)
    s = player.d.get('ordeal_shop')
    if not s or s.get('week') != week or s.get('tier') != tier:
        rng = random.Random('%s:%d:%d' % (player.account_id, week, tier))
        s = {'week': week, 'tier': tier,
             'goods': [[uid, 0] for uid in roll_stock(tier, rng)]}
        player.d['ordeal_shop'] = s
    return s


def shop_info(player, now=None):
    s = shop(player, now)
    _, start, end = state.calendar_season(state.SEASON_WEEKLY, now)
    return TYPES['NGOrdealTowerShop'](
        tmOpenSeasonStart=start, tmOpenSeasonEnd=end, tmNextSeasonOpen=end,
        vecGoodsInfo=[TYPES['NGOrdealTowerShopGoods'](GoodsUID=int(uid), BuyCount=int(n))
                      for uid, n in s['goods']])


def buy(player, goods_uid, now=None):
    """(rewards, 0) or (None, error)."""
    from .errors import Err
    s = shop(player, now)
    entry = next((g for g in s['goods'] if int(g[0]) == int(goods_uid)), None)
    r = goods_row(goods_uid)
    if entry is None or r is None:
        return None, SHOP_OVER
    if int(entry[1]) >= max(to_int(r['buyCount'], 1), 1):
        return None, Err.LOCKED
    if not player.spend_resource(to_int(r['costType1']), to_int(r['costValue'], 0),
                                 to_int(r.get('costType2'), -1)):
        return None, Err.NOT_ENOUGH
    entry[1] = int(entry[1]) + 1
    return [(to_int(r['resourceType1']), to_int(r['resourceType2']), -1,
             to_int(r['resourceValue'], 0))], 0
