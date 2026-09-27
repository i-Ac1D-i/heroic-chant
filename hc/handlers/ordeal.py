"""The Trial Tower: StartOrdealTowerReq .. BuyOrdealTowerShopGoodsReq
(30164-30169).

See hc/game/ordeal.py for the rules as the client has them.
"""
import logging

from ..net import handler
from ..game import state, rewards, ordeal
from ..game.errors import Err

log = logging.getLogger('hc.ordeal')


def _grant(p, paid):
    before = len(p.d['units'])
    rewards.grant(p, paid)
    return [state.unit_info(u) for u in p.d['units'][before:]]


@handler(30164)
async def ordeal_start(s, a):
    p, did = s.player, int(a['_DungeonID'])
    party = a['_vecPartyInfo'] or []
    err = ordeal.can_start(p, did)
    if err:
        log.info('trial tower floor %d refused: %d', did, err)
        await s.send(40180, err, did, party)
        return
    if party:
        p.set_party(party[0].SlotType,
                    [{'slot_index': u.SlotIndex, 'unit_uid': u.UnitUID,
                      'skill_on_off': u.SkillOnOff} for u in party])
    p.d['ordeal_active'] = did
    p.save()
    log.info('trial tower floor %d start', did)
    await s.send(40180, Err.OK, did, party)


@handler(30165)
async def ordeal_end(s, a):
    """Won or lost, the battle counts as one of the day's plays; a win
    clears the floor.  The clear reward is claimed separately (30166)."""
    p, did = s.player, int(a['_DungeonID'])
    active = p.d.pop('ordeal_active', None)
    won = int(a['_ClearType']) == ordeal.WIN
    group = ordeal.group_of(did)
    if active != did or group is None:
        log.info('trial tower end for floor %d with no battle started -- ignored', did)
    else:
        ordeal.add_play(p, group)
        if won:
            ordeal.record_win(p, did)
    p.save()
    log.info('trial tower floor %d %s', did, 'cleared' if won else 'lost')
    await s.send(40181, Err.OK, state.resource_sync(p), ordeal.play_count_infos(p))


@handler(30166)
async def ordeal_clear_reward(s, a):
    p, did = s.player, int(a['_DungeonID'])
    paid = ordeal.claim(p, did)
    if paid is None:
        log.info('trial tower floor %d: no clear reward to claim', did)
        await s.send(40182, Err.INVALID, did, state.resource_sync(p))
        return
    new_units = _grant(p, paid)
    p.save()
    log.info('trial tower floor %d clear reward: %s', did, paid)
    await s.send(40182, Err.OK, did, state.resource_sync(p, vecAddUnitInfo=new_units))


@handler(30167)
async def ordeal_wait_reward(s, a):
    p = s.player
    paid, err = ordeal.wait_reward(p)
    if paid is None:
        log.info('trial tower standby reward refused: %d', err)
        await s.send(40183, err, state.resource_sync(p))
        return
    new_units = _grant(p, paid)
    p.save()
    log.info('trial tower standby reward: %s', paid)
    await s.send(40183, Err.OK, state.resource_sync(p, vecAddUnitInfo=new_units))


@handler(30168)
async def ordeal_shop_info(s, a):
    p = s.player
    info = ordeal.shop_info(p)
    p.save()
    await s.send(40184, Err.OK, info)


@handler(30169)
async def ordeal_shop_buy(s, a):
    p, uid = s.player, int(a['_GoodsUID'])
    paid, err = ordeal.buy(p, uid)
    if paid is None:
        log.info('trial tower shop: goods %d refused: %d', uid, err)
        await s.send(40185, err, ordeal.shop_info(p), state.resource_sync(p))
        return
    new_units = _grant(p, paid)
    p.save()
    log.info('trial tower shop: bought %d -> %s', uid, paid)
    await s.send(40185, Err.OK, ordeal.shop_info(p),
                 state.resource_sync(p, vecAddUnitInfo=new_units))
