"""Command Center leads: Sarah Coldwell, Catherine, Demitt."""
import logging

from ..net import handler
from ..data.tables import TABLES, to_int
from ..game import state
from ..game.errors import Err

log = logging.getLogger('hc.commander')


def _costs(row):
    """Read (resourceID, type2, value) triples off a ResourceID_N cost row.
    Works for the 2-slot and 3-slot tables alike."""
    out = []
    i = 1
    while ('ResourceID_%d' % i) in row:
        rid = to_int(row.get('ResourceID_%d' % i), -1)
        val = to_int(row.get('Val_%d' % i), 0)
        if rid >= 0 and val > 0:
            out.append((rid, to_int(row.get('ResourceID_%d_Type2' % i), -1), val))
        i += 1
    return out


def _pay(player, costs):
    if any(player.get_resource(r, t2) < v for r, t2, v in costs):
        return False
    for r, t2, v in costs:
        player.add_resource(r, -v, t2)
    return True


def _level_up_commander(p, commander_id):
    """commandersStatInfo has one row per (CommandersID, Level); IDs 0/1/2
    are Sarah Coldwell/Catherine/Demitt. Each row's cost is what it takes to
    reach that level.

    The real client also caps this by the Command Center building's level,
    which isn't modelled here - left uncapped rather than guessed at, same
    as the account-rank shortcut used for hero levelling.

    Returns (Err, commander). commander is only meaningful on Err.OK; the
    caller still has to save.
    """
    c = p.find_commander(commander_id)
    if c is None:
        return Err.NOT_FOUND, None
    target = c['level'] + 1
    row = next((r for r in TABLES.rows('commandersStatInfo', 'CommandersID', c['id'])
               if to_int(r['Level']) == target), None)
    if row is None:
        log.info('commander %d is already at max level %d', c['id'], c['level'])
        return Err.INVALID, None
    if not _pay(p, _costs(row)):
        return Err.NOT_ENOUGH, None
    c['level'] = target
    return Err.OK, c


@handler(30030)
async def commander_level_up(s, a):
    """CommanderLvUpReq. Kept in case something still sends it, but the real
    Level Up button on the Command Center screen sends CommandersLevelUpReq
    (30300) below instead."""
    p = s.player
    err, c = _level_up_commander(p, a['commanderID'])
    if err != Err.OK:
        await s.send(40035, err, state.resource_sync(p))
        return
    p.save()
    await s.send(40035, Err.OK, state.resource_sync(
        p, vecChangeCommander=[state.commander_info(c)],
        vecAddCommandersInfo=[state.commanders_info(c)]))


@handler(30300)
async def commanders_level_up(s, a):
    """The actual Level Up button on the Command Center screen, confirmed
    from a live client log - the client sends this, not CommanderLvUpReq."""
    p = s.player
    cid = a['_CommanderID']
    err, c = _level_up_commander(p, cid)
    if err != Err.OK:
        await s.send(40322, err, cid, state.resource_sync(p))
        return
    p.save()
    await s.send(40322, Err.OK, cid, state.resource_sync(
        p, vecChangeCommander=[state.commander_info(c)],
        vecAddCommandersInfo=[state.commanders_info(c)]))


@handler(30299)
async def command_center_level_up(s, a):
    """Levels the Command Center building itself, not a specific commander -
    commandCenterStatInfo has one row per level (0-230), no id needed since
    there's only one per account.

    A commander's level is supposed to be capped by this building's level,
    but that isn't wired up yet.
    """
    p = s.player
    level = p.d.get('command_center_level', 0)
    target = level + 1
    row = TABLES.row('commandCenterStatInfo', 'Level', target)
    if row is None:
        log.info('Command Center is already at max level %d', level)
        await s.send(40321, Err.INVALID, state.resource_sync(p))
        return
    costs = _costs(row)
    if not _pay(p, costs):
        await s.send(40321, Err.NOT_ENOUGH, state.resource_sync(p))
        return
    p.d['command_center_level'] = target
    p.save()
    await s.send(40321, Err.OK,
                 state.resource_sync(p, vecAddCommandCenterInfo=[state.command_center_info(p)]))
