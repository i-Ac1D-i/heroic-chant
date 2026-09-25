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


@handler(30301)
async def commanders_party_change(s, a):
    """Put a Command Center lead in a party, or take them out (-1).

    Sent by CommandersSelectUI (and the boss-dungeon team screen).  It had no
    handler, so the client waited on an Ack that never came and no commander
    could be brought into battle.  The battle itself reads the assignment on
    the client (NMUserInfo.GetCommanderPartyInfo), so storing it and echoing
    it back as vecAddCommandersPartyInfo is all the server does.

    For the three Wave Raid parties (40000-40002) the client allows a lead in
    only one of them, and clears the other party first with its own -1
    request -- so nothing here needs to enforce that.
    """
    p = s.player
    party_type = int(a['_PartyType'])
    commander_id = int(a['_CommanderID'])
    if commander_id != -1 and p.find_commander(commander_id) is None:
        log.info('party %d: commander %d is not owned', party_type, commander_id)
        await s.send(40323, Err.NOT_FOUND, party_type, commander_id,
                     state.resource_sync(p))
        return
    parties = p.d.setdefault('commander_party', {})
    if commander_id == -1:
        parties.pop(str(party_type), None)
    else:
        parties[str(party_type)] = commander_id
    p.save()
    log.info('party %d now brings commander %d', party_type, commander_id)
    # The -1 goes out too: the client upserts by PartyType, so that is what
    # clears its copy.
    await s.send(40323, Err.OK, party_type, commander_id, state.resource_sync(
        p, vecAddCommandersPartyInfo=[state.commanders_party_info(party_type,
                                                                  commander_id)]))


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
