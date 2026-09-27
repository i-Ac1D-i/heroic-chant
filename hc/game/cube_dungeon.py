"""The Cube Dungeon: a 50-floor climb that changes with the season.

Read off the client:

* It only opens with a type-5037 event: ``NMEvent.CheckEventCubeDungeon5037Event``
  @0x1F3D660 looks for one whose Arg1 parses as a number -- the season -- and
  ``CubeDungeonListScene.SetCubeDungeonSeason`` runs the screen (and its
  countdown, the event's tmEnd) off it.  Nothing sent that event, which is why
  the mode never opened.
* ``CubeDungeonInfo`` (json) holds each season's floors (seasons 2-25, 50
  floors each): the stage, its stamina cost (129), up to three hero conditions
  (checked by the client, errorString 2446) and the floor's two rewards.  The
  stages have no ``rewardClear`` rows; the floor reward is the pay.
* Where the player is: ``NGCubeDungeonInfo`` (Season, ClearFloor, Received) in
  ``NGLogInAck02.userCubeDungeonInfo`` and every Cube Ack.
  ``CubeDungeonListScene.SetCubeDungeon`` @0x1D4DDD0 puts the player on
  ClearFloor + 1 once Received is 1 (the cleared floor's reward taken), else
  back on ClearFloor -- the same "claim before you climb" as the Trial Tower.
* ``StartCubeDungeonReq`` (floor, stage) from the team screen;
  ``EndCubeDungeonReq`` after every battle, won or lost (ClearType 1 is a
  win; _Type is always 0); ``GetCubeDungeonFloorRewardReq`` (floor).

The season: ``cube_dungeon.season`` in the settings.  0 (the default) rotates
through the 24 seasons a calendar month at a time; a number pins one.  A new
season starts the climb over.
"""
from datetime import datetime, timedelta

from ..data.tables import TABLES, to_int
from ..protocol.dto import TYPES
from ..settings import SETTINGS
from . import state

EVENT = 5037
WIN = 1
CONDITION = 2446             # errorString: the hero conditions are not met


def seasons():
    return sorted({to_int(r['Season']) for r in TABLES.json('CubeDungeonInfo')})


def current_season(now=None):
    """(season, start, end)."""
    now = now or datetime.utcnow()
    pinned = int(SETTINGS.get('cube_dungeon.season', 0) or 0)
    all_ = seasons()
    if pinned in all_:
        return pinned, now - timedelta(days=1), now + timedelta(days=365)
    _, start, end = state.calendar_season(state.SEASON_MONTHLY, now)
    return all_[(now.year * 12 + now.month) % len(all_)], start, end


def event(now=None):
    season, start, end = current_season(now)
    return TYPES['NGEventInfo'](UID=EVENT * 1000 + season, ID=EVENT,
                                EventListUID=EVENT * 1000 + season, tmStart=start, tmEnd=end,
                                Arg1=str(season), Arg2='', Arg3='', Arg4='', Arg5='', Arg6='',
                                Arg7='', Arg8='', Arg9='')


def floor(season, number):
    for r in TABLES.rows('CubeDungeonInfo', 'Season', int(season), source='json'):
        if to_int(r['Floor']) == int(number):
            return r
    return None


def top(season):
    return max((to_int(r['Floor']) for r in TABLES.rows('CubeDungeonInfo', 'Season',
                                                       int(season), source='json')), default=0)


def progress(player, now=None):
    """The player's {'season', 'clear', 'received'}, started over when the
    season has moved on."""
    season = current_season(now)[0]
    c = player.d.get('cube_dungeon')
    if not c or c.get('season') != season:
        c = {'season': season, 'clear': 0, 'received': 0}
        player.d['cube_dungeon'] = c
    return c


def info(player, now=None):
    c = progress(player, now)
    return TYPES['NGCubeDungeonInfo'](Season=int(c['season']), ClearFloor=int(c['clear']),
                                      Received=int(c['received']))


def next_floor(player, now=None):
    c = progress(player, now)
    if c['clear'] == 0:
        return 1
    if not c['received']:
        return c['clear']
    return min(c['clear'] + 1, top(c['season']))


def can_start(player, number, dungeon_id, now=None):
    from .errors import Err
    c = progress(player, now)
    r = floor(c['season'], number)
    if r is None or to_int(r['DungeonID']) != int(dungeon_id):
        return Err.NOT_FOUND
    if int(number) != c['clear'] + 1 or (c['clear'] and not c['received']):
        return Err.LOCKED
    if player.get_resource(to_int(r['CostType1']), to_int(r.get('CostType2'), -1)) \
            < to_int(r['CostValue'], 0):
        return Err.NOT_ENOUGH
    return 0


def charge(player, number, now=None):
    r = floor(progress(player, now)['season'], number)
    player.spend_resource(to_int(r['CostType1']), to_int(r['CostValue'], 0),
                          to_int(r.get('CostType2'), -1))


def record_win(player, number, now=None):
    c = progress(player, now)
    if int(number) == c['clear'] + 1:
        c['clear'], c['received'] = int(number), 0


def claim(player, number, now=None):
    """The floor's two rewards, once, for the floor just cleared; returns the
    rewards or None."""
    c = progress(player, now)
    if int(number) != c['clear'] or c['clear'] == 0 or c['received']:
        return None
    r = floor(c['season'], number)
    c['received'] = 1
    out = []
    for k in (1, 2):
        t1 = to_int(r.get('Reward%d_Type1' % k), -1)
        n = to_int(r.get('Reward%d_Value' % k), 0)
        if t1 >= 0 and n > 0:
            out.append((t1, to_int(r.get('Reward%d_Type2' % k), -1), -1, n))
    return out
