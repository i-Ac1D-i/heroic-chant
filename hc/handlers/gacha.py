"""Hero summoning (Dimension Gacha)."""
import logging

from ..net import handler
from ..protocol.dto import TYPES
from ..data.tables import TABLES, to_int
from ..game import state, gacha
from ..game.errors import Err

log = logging.getLogger('hc.gacha')


def _name(unit_id):
    row = TABLES.unit(unit_id) or {}
    try:
        return TABLES.strings()[to_int(row.get('NameID'))]
    except (KeyError, TypeError):
        return str(unit_id)


@handler(30065)
async def instant_summon(s, a):
    p = s.player
    gid, count = a['GachaID'], max(a['SummonCount'], 1)

    price = gacha.cost(gid, count)
    if price is not None:
        t1, t2, amount = price
        if not p.spend_resource(t1, amount, t2):
            log.info('summon refused: banner %d needs %d of resource %d/%d',
                     gid, amount, t1, t2)
            await s.send(40070, Err.NOT_ENOUGH, state.resource_sync(p), TYPES['NGPairInt2'](),
                         TYPES['NGIntIntInt'](), gacha.summons_count(p, gid),
                         TYPES['NGGachaSummonFreeEvent'](),
                         TYPES['NGGachaChoiceCubeInfo'](), [])
            return

    results = gacha.roll(gid, count)
    added = [p.add_unit(uid, rareness=rare) for uid, rare in results]
    p.bump_summon_count(gid, len(added))
    p.save()
    log.info('banner %d x%d -> %s', gid, count,
             ', '.join('%s(R%d)' % (_name(u['id']), u['rareness']) for u in added))

    await s.send(
        40070, 0,
        state.resource_sync(p, vecAddUnitInfo=[state.unit_info(u) for u in added]),
        TYPES['NGPairInt2'](), TYPES['NGIntIntInt'](),
        gacha.summons_count(p, gid), TYPES['NGGachaSummonFreeEvent'](),
        TYPES['NGGachaChoiceCubeInfo'](),
        # The summon result reel: Unit results are ResourceType.Unit with the
        # unit id in Type2.
        [state.resource_info(1, u['id'], -1, 1) for u in added])


@handler(30062)
async def join_dimension_gacha(s, a):
    """Opening the summon screen, and the free "Touch!" pull.

    actionType 0 is just opening the banner; anything else is the player
    actually pulling the free Time Cube summon.
    """
    p = s.player
    gid, action = a['openGachaID'], a['actionType']
    log.info('dimension gacha: banner=%s action=%s', gid, action)

    # openGachaID < 0 is just "the screen opened"; a real banner id is the
    # player actually pulling the cube.  actionType stays 0 either way, so it
    # cannot be used to tell them apart.
    added = []
    if gid > 0:
        price = gacha.cost(gid, 1)
        if price is not None:
            t1, t2, amount = price
            if not p.spend_resource(t1, amount, t2):
                log.info('cube pull refused: banner %d needs %d of resource %d/%d',
                         gid, amount, t1, t2)
                await s.send(40067, Err.NOT_ENOUGH, state.resource_sync(p),
                             gacha.summons_count(p, gid), TYPES['NGPairInt2'](),
                             TYPES['NGIntIntInt'](), TYPES['NGGachaChoiceCubeInfo']())
                return
        for uid, rare in gacha.roll(gid, 1):
            added.append(p.add_unit(uid, rareness=rare))
        if added:
            p.bump_summon_count(gid, len(added))
            p.save()
            log.info('cube pull on banner %d -> %s', gid,
                     ', '.join('%s(R%d)' % (_name(u['id']), u['rareness']) for u in added))

    # The result page waits on NGGachaChoiceCubeInfo naming the cube that was
    # opened; an empty one reads as "the Dimension Gacha has dissipated" and
    # the client sits on "Waiting for server response...".
    cube = TYPES['NGGachaChoiceCubeInfo'](
        season=0, gachaID=int(gid) if gid > 0 else 0,
        summonCount=len(added),
        summonHeroID=added[0]['id'] if added else 0)
    await s.send(40067, Err.OK,
                 state.resource_sync(p, vecAddUnitInfo=[state.unit_info(u) for u in added]),
                 gacha.summons_count(p, gid), TYPES['NGPairInt2'](),
                 TYPES['NGIntIntInt'](), cube)


@handler(30063)
async def buy_dimension_gacha(s, a):
    """Tapping one of the cubes on the summon screen."""
    p = s.player
    gid, slot_id = a['GachaID'], a['slotID']
    taken = p.take_gacha_slot(gid, slot_id)
    if taken is None:
        log.info('cube %s on banner %s already taken', slot_id, gid)
        await s.send(40068, Err.NOT_FOUND, state.resource_sync(p))
        return

    price = gacha.cost(gid, 1)
    if price is not None:
        t1, t2, amount = price
        if not p.spend_resource(t1, amount, t2):
            taken['sold'] = False          # give the cube back
            log.info('cube refused: banner %s needs %d of resource %d/%d',
                     gid, amount, t1, t2)
            await s.send(40068, Err.NOT_ENOUGH, state.resource_sync(p))
            return

    unit = p.add_unit(taken['unit'], rareness=taken['rareness'])
    p.bump_summon_count(gid, 1)
    p.save()
    log.info('cube %s on banner %s -> %s (R%d)', slot_id, gid,
             _name(unit['id']), unit['rareness'])
    await s.send(40068, Err.OK,
                 state.resource_sync(p, vecAddUnitInfo=[state.unit_info(unit)]))


@handler(30064)
async def reduce_summon_timer(s, a):
    await s.send(40069, Err.OK, state.resource_sync(s.player))


@handler(30285)
async def gacha_simulation_no_update(s, a):
    await s.send(40307, Err.OK)
