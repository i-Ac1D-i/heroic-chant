"""Dimension Crack (the client's TrainingTower): five towers of 100 floors.

Read off the client:

* ``TrainingTowerList`` (json) holds the floors: dungeon_ID 20001.. for tower
  1 up to 20500 for tower 5, Stage_index 1..100, CheckPoint, NextDungeonID
  (-1 on floor 100) and UseTicketCount (1 everywhere).  The drops are the
  ordinary stage tables (``rewards.roll``).
* Where a player is: resource ``TrainingTowerStage`` (29) keyed by tower is
  the index of the floor to play next.  ``NMContents.GetTrainingTowerData``
  (group, that index) is the floor the team screen fights and the select
  screen shows as current; the floors below it show as cleared.  At 0 there
  is no floor at all, so it starts at 1.
* The ticket is ``TrainingTowerTicket`` (28) keyed by tower:
  ``TrainingTowerDungeonTeamSelectSceneInit.Start`` @0x1B61A0C builds the cost
  as NGResourceInfo(28, groupID, UseTickCount).  They refill daily
  (ResourceRefresh, 3 a tower -- hc/game/refills.py).
* A run climbs floors back to back on one ticket.  ``TrainingTowerPlayScene
  .GameEnd`` @0x1A6C4B8, after a win, moves straight on to the next floor --
  unless the floor just cleared is a CheckPoint (``PBTrainingTowerCheckPoint
  EndAnimation.bSave``), in which case it reports it
  (``TrainingTowerEndReq``) and the run ends.  After a win on the top floor it
  reports that floor; after a loss it reports the floor *before* the lost one
  (``GetDungeonPreDungeonID``).  So an End names the highest floor cleared.
* The select screen shows "next save point: floor N"
  (``GetTrainingTowerNextSaveStage``: the next CheckPoint up the chain).
* Skip (``GetTrainingTowerCheckPointRewardReq``, a list of towers) is sent
  from the one-tower skip popup and the all-towers one.  The all-towers popup
  (``TrainingTowerDungeonSelectSceneInit.<Start>b__18_3``) previews each
  tower's current floor's fixed rewards, and its button is lit for any tower
  whose current floor exists and whose ticket the player has
  (``UpdateSkipBtn``).

INFERRED -- the retail rules lived on the server:

* Progress is saved at checkpoints: an End that names a CheckPoint floor (or
  the top floor) moves the tower on to the floor after it; one that names
  anything else pays for the floors cleared but leaves the tower where it was,
  so the next run starts again after the last checkpoint.  That is what "save
  point" means on the select screen.
* Every floor cleared in a run pays its drops once, first-clear drops the
  first time ever (DungeonClearCount keyed by the floor, as story stages).
* A skip costs one ticket per tower and pays that tower's current floor's
  repeat drops -- the rewards the popup previews -- without moving it.  It
  needs a saved checkpoint: the client has errorString 1066 "You can't skip
  because there is no checkpoint", so a tower still on floor 1 cannot skip.
* The event-day bonus (EEVENT 102/104 rate on one tower's reward) is not sent.
"""
from ..data.tables import TABLES, to_int
from .enums import CollectionType

GROUPS = (1, 2, 3, 4, 5)
NO_TICKET = 1138            # "You don't have enough Dimension Crack Tickets."
NO_CHECKPOINT = 1066        # "You can't skip because there is no checkpoint."
STAGE = 29                  # ResourceType.TrainingTowerStage, keyed by tower
TICKET = 28                 # ResourceType.TrainingTowerTicket, keyed by tower
TOP = 100


def row(dungeon_id):
    return TABLES.row('TrainingTowerList', 'dungeon_ID', int(dungeon_id), source='json')


def floor(group, index):
    """The TrainingTowerList row for tower ``group`` floor ``index``, or None."""
    for r in TABLES.rows('TrainingTowerList', 'TrainingTowerGroup', int(group), source='json'):
        if to_int(r['Stage_index']) == int(index):
            return r
    return None


def group_of(dungeon_id):
    r = row(dungeon_id)
    return to_int(r['TrainingTowerGroup']) if r else None


def index_of(dungeon_id):
    r = row(dungeon_id)
    return to_int(r['Stage_index']) if r else None


def is_save_point(r):
    return to_int(r.get('CheckPoint'), 0) == 1 or to_int(r.get('NextDungeonID'), -1) < 0


def ticket_cost(r):
    return max(to_int(r.get('UseTicketCount'), 1), 0)


def current(player, group):
    """The index of the floor to play next in a tower (at least 1)."""
    return max(player.get_resource(STAGE, int(group)), 1)


def backfill(player):
    """Every tower starts on floor 1; without it the client has no floor."""
    changed = False
    for g in GROUPS:
        if player.get_resource(STAGE, g) < 1:
            player.add_resource(STAGE, 1 - player.get_resource(STAGE, g), g)
            changed = True
    return changed


def set_current(player, group, index):
    index = max(1, min(int(index), TOP))
    have = player.get_resource(STAGE, int(group))
    if have != index:
        player.add_resource(STAGE, index - have, int(group))


def cleared_before(player, dungeon_id):
    return player.get_collection(CollectionType.DungeonClearCount, t2=int(dungeon_id)) > 0


def record_clear(player, dungeon_id):
    """Count a floor cleared; True the first time ever."""
    first = not cleared_before(player, dungeon_id)
    player.add_collection(CollectionType.DungeonClearCount, 1, t2=int(dungeon_id))
    return first
