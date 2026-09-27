"""The Hero Dungeon: a short series of stages per hero, each clearable once a day.

Read off the client:

* ``HeroDungeonList`` (json) holds the stages: dungeon_ID (10001...), the
  hero (UnitID), stage_index and ``preDungeonID`` chaining them.  Their drops
  are the ordinary stage tables (``rewards.roll``).
* Entering costs one Hero Dungeon Ticket: ``HeroDungeonTeamSelectInit.Start``
  @0x1EBF7BC sets its ``ticketCompareReward`` to NGResourceInfo(26, -1, 1),
  and ``InGameStart`` compares against it (offering to buy more) before
  sending ``HeroDungeonStartReq``.
* A stage is clearable once a day: ``NMUserInfo.IsEnablePlayHeroDungeonWithUnitID``
  @0x139BF60 lets a stage be played when it was never cleared
  (collection ``HeroDungeonClearCount`` (4) keyed by the stage is 0) and it is
  open, or when it was cleared but not today; "today" is
  ``CheckClearHeroDungeonToday`` @0x139B974, ``NGHeroDungeonClearInfo.ClearDailySeason``
  equal to the Daily season value.  Those go out at login
  (``NGLogInAck01.vecHeroDungeonClearInfo``) and after every clear.
* ``HeroDungeonEndReq`` carries a list of stages: one from a played battle
  (``HeroDungeonPlayScene.GameEnd``), or several from the skip popups
  (``PopupboxHeroDungeonSkip`` / ``...BatchSkipReward``, ClearType 3 = Clear).

INFERRED: a skipped stage costs one ticket, like an entry, and must have been
cleared before.
"""
from datetime import datetime

from ..data.tables import TABLES, to_int
from ..protocol.dto import TYPES
from .enums import CollectionType
from . import state

TICKET = (26, -1, 1)           # ResourceType.HeroDungeonTicket, one per stage
CLEAR_SKIP = 3                 # ClearType.Clear, what the skip popups send


def row(dungeon_id):
    return TABLES.row('HeroDungeonList', 'dungeon_ID', int(dungeon_id), source='json')


def today(now=None):
    return state.season_value(state.SEASON_DAILY, now)


def cleared_ever(player, dungeon_id):
    return player.get_collection(CollectionType.HeroDungeonClearCount,
                                 t2=int(dungeon_id)) > 0


def _clears(player):
    return player.d.setdefault('hero_dungeon', {})


def cleared_today(player, dungeon_id, now=None):
    return int(_clears(player).get(str(int(dungeon_id)), -1)) == today(now)


def can_enter(player, dungeon_id, now=None):
    """(error or 0).  The client does the hero/awakening conditions
    (herodungeonOpenSpec); here: it exists, the stage before it is cleared,
    and it is not cleared today."""
    from .errors import Err
    r = row(dungeon_id)
    if r is None:
        return Err.NOT_FOUND
    pre = to_int(r.get('preDungeonID'), -1)
    if pre > 0 and not cleared_ever(player, pre):
        return Err.LOCKED
    if cleared_today(player, dungeon_id, now):
        return Err.INVALID
    return 0


def record_clear(player, dungeon_id, now=None):
    """Mark a stage cleared today; returns True if it is the first time ever."""
    first = not cleared_ever(player, dungeon_id)
    player.add_collection(CollectionType.HeroDungeonClearCount, 1, t2=int(dungeon_id))
    _clears(player)[str(int(dungeon_id))] = today(now)
    return first


def clear_info(dungeon_id, season):
    return TYPES['NGHeroDungeonClearInfo'](DungeonID=int(dungeon_id),
                                           ClearDailySeason=int(season))


def clear_infos(player):
    return [clear_info(did, season) for did, season in sorted(_clears(player).items(),
                                                              key=lambda x: int(x[0]))]
