"""Polite refusals for game modes this server does not run yet.

Every request here used to go unanswered, so the client sat waiting on an Ack
that never came -- the Advent Boss "freeze", the Hero Dungeon and Trial Tower
"unable to challenge".  Now each gets its own Ack with an error code, and the
client shows the matching message from its own `errorString` table and stays
usable.  Nothing is charged and nothing is recorded.

Only requests a player sends on purpose are listed -- every one was checked
with an xref to be sent from a team screen's Start button (InGameStart), a
world-map button, or a popup's confirm button.  Requests the client sends by
itself when a screen opens (AlienDungeonOneInfoReq from DungeonListSceneInit,
WorldRaidGameStartReq from WorldRaidPartyTeamSelectSceneInit.Start,
CheckWaveRaid2InfoReq from the world map) are deliberately left out: an error
there would pop a dialog every time the screen opened.

Each Ack handler's error branch is ShowServerError and return (checked in the
disassembly); a false return from a handler only makes the stub print a
console line.

Implementing one of these modes means deleting its line here.
"""
import logging

from ..net import handler, HANDLERS
from ..protocol.dto import PACKET_SPEC, _default
from ..game import state

log = logging.getLogger('hc.not_open')

# Messages, from the client's errorString table:
HERO_DUNGEON = 1060   # "You cannot enter Hero Dungeon."
FLOOR = 1157          # "This floor is unavailable at the moment."
EVENT = -723          # "This event is not open at the moment.(-723)"

# request id, Ack id, error code, what the player tried
NOT_OPEN = (
    (30158, 40174, FLOOR, 'Hard story stage'),
    (30294, 40316, EVENT, 'Other World Boss'),
    (30078, 40084, EVENT, 'Other World Boss (alien)'),
    (30316, 40337, EVENT, 'Wave Raid'),
    (30352, 40375, EVENT, 'Wave Raid 2'),
    (30193, 40209, EVENT, 'Event dungeon'),
    (30302, 40324, EVENT, 'Collaboration dungeon'),
    (30045, 40050, EVENT, 'Dimension Gap'),
    (30274, 40296, EVENT, "Heart Heater's Quest House"),
    (30146, 40162, EVENT, 'Guild Labyrinth (open stage)'),
    (30149, 40165, EVENT, 'Guild Labyrinth (battle)'),
    # Sent by the arena picker (PopupboxArenaSelect) and UIButtonMoveScene,
    # which only move on to TagPvPScene on success -- so an error leaves the
    # player on the picker instead of waiting forever.
    (30173, 40189, EVENT, 'Tag Arena'),
)


def refusal_args(ack_id, error, player):
    """The Ack's fields: the error first, defaults for the rest, and the usual
    resource sync wherever it carries an NGCheckServerInfo."""
    args = []
    for i, f in enumerate(PACKET_SPEC[ack_id]['fields']):
        if i == 0:
            args.append(int(error))
        elif f.get('type') == 'NGCheckServerInfo':
            args.append(state.resource_sync(player))
        else:
            args.append(_default(f))
    return args


def _make(req_id, ack_id, error, label):
    async def refuse(s, a):
        log.info('%s is not available on this server yet (packet %d) -- answered %d',
                 label, req_id, error)
        await s.send(ack_id, *refusal_args(ack_id, error, s.player))
    refuse.__name__ = 'not_open_%d' % req_id
    refuse.__doc__ = '%s: not implemented; refused with error %d.' % (label, error)
    return handler(req_id)(refuse)


# Imported last (see __init__), and never over a real handler.
for _req, _ack, _err, _label in NOT_OPEN:
    if _req not in HANDLERS:
        _make(_req, _ack, _err, _label)
