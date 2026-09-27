"""The Cube Dungeon: StartCubeDungeonReq (30335), EndCubeDungeonReq (30336)
and GetCubeDungeonFloorRewardReq (30337).

See hc/game/cube_dungeon.py for the rules as the client has them.
"""
import logging

from ..net import handler
from ..game import state, rewards, cube_dungeon as cube
from ..game.errors import Err

log = logging.getLogger('hc.cube_dungeon')


@handler(30335)
async def cube_start(s, a):
    p, number, did = s.player, int(a['_Floor']), int(a['_DungeonID'])
    err = cube.can_start(p, number, did)
    if err:
        log.info('cube dungeon floor %d refused: %d', number, err)
        await s.send(40359, err, state.resource_sync(p))
        return
    cube.charge(p, number)
    p.d['cube_active'] = number
    p.save()
    log.info('cube dungeon floor %d start (%d)', number, did)
    await s.send(40359, Err.OK, state.resource_sync(p))


@handler(30336)
async def cube_end(s, a):
    p, number = s.player, int(a['_Floor'])
    active = p.d.pop('cube_active', None)
    won = int(a['_ClearType']) == cube.WIN
    if active != number:
        log.info('cube dungeon end for floor %d with no battle started -- ignored', number)
    elif won:
        cube.record_win(p, number)
    p.save()
    log.info('cube dungeon floor %d %s', number, 'cleared' if won else 'lost')
    await s.send(40360, Err.OK, cube.info(p), state.resource_sync(p))


@handler(30337)
async def cube_floor_reward(s, a):
    p, number = s.player, int(a['_Floor'])
    paid = cube.claim(p, number)
    if paid is None:
        log.info('cube dungeon floor %d: no reward to take', number)
        await s.send(40361, Err.INVALID, cube.info(p), state.resource_sync(p))
        return
    before = len(p.d['units'])
    rewards.grant(p, paid)
    new_units = [state.unit_info(u) for u in p.d['units'][before:]]
    p.save()
    log.info('cube dungeon floor %d reward: %s', number, paid)
    await s.send(40361, Err.OK, cube.info(p), state.resource_sync(p, vecAddUnitInfo=new_units))
