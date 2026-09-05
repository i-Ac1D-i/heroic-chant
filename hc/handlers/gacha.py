"""Hero summoning (Dimension Gacha)."""
import asyncio
import logging
import os

from ..net import handler
from ..protocol.dto import TYPES
from ..data.tables import TABLES, to_int
from ..game import state, gacha, rewards
from ..game.errors import Err

log = logging.getLogger('hc.gacha')

# Diagnostic only.  The client throws a NullReferenceException somewhere in the
# summon flow and the device clock and the server clock are ~1.5 s apart, which
# is too close to tell from timestamps whether the throw happens before or
# after our Ack lands.  Holding the Ack back by several seconds makes the
# ordering unambiguous in `adb logcat`.  Leave at 0 unless you are chasing that.
ACK_DELAY = float(os.environ.get('HC_GACHA_ACK_DELAY', '0') or 0)

# A summon is answered with the same Ack *shape* as a screen-open: the whole
# wallet rather than a delta, and NGGachaDaySummonsCount.GachaID = -1.  That is
# not cosmetic -- it is the only shape the client does not throw on.
#
# Every other combination tried made JoinDimensionGachaAck raise
#     NGNetGameServer OK, Object reference not set to an instance of an object.
# inside the client (see `adb logcat | grep Unity`), which leaves
# DimensionGachaTabHeroUI.bSendPacket set -- GachaSummon @0x1A3C3E4 opens with
# `if (bSendPacket) return;` -- so the summon button dies and the screen cannot
# be dismissed.  Ruled out one at a time, none of them the cause:
# _GachaChoiceCubeInfo, vecAddUnitInfo, vecChangeDimensionGacha, and the reward
# contents.  What has never been varied independently, and is therefore where
# the next session should start, is exactly the two things this shape changes:
#
#   1. vecAddResourceInfo carrying the full wallet instead of only what changed
#   2. _GachaSummonsCount.GachaID being -1 instead of the banner id
#      (NMUserInfo.AddGachaLimitInfo @0x13A3104 is the first thing the Ack
#      handler @0x14EBE38 does with it)
#
# Cost of the workaround: a genuinely *new* hero is not announced in
# vecAddUnitInfo, so it only appears after a relogin.  Duplicates -- which is
# every pull once the roster is complete -- are unaffected, because the client
# picks the shard up by diffing the wallet.  Set HC_GACHA_RICH_ACK=1 to send
# the fuller Ack again while chasing the real fix.
RICH_ACK = os.environ.get('HC_GACHA_RICH_ACK', '0') in ('1', 'yes', 'true')


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
    added, reel = [], []
    for uid, rare in results:
        unit, shards = gacha.grant(p, uid, rare)
        if unit is not None:
            added.append(unit)
            # The summon result reel: Unit results are ResourceType.Unit with
            # the unit id in Type2.
            reel.append(state.resource_info(1, unit['id'], -1, 1))
        else:
            rewards.grant(p, shards)
            reel.extend(state.resource_info(*sh) for sh in shards)
    p.bump_summon_count(gid, len(results))
    p.save()
    log.info('banner %d x%d -> %d new, %d duplicate',
             gid, count, len(added), len(results) - len(added))

    await s.send(
        40070, 0,
        state.resource_sync(p, vecAddUnitInfo=[state.unit_info(u) for u in added]),
        TYPES['NGPairInt2'](), TYPES['NGIntIntInt'](),
        gacha.summons_count(p, gid), TYPES['NGGachaSummonFreeEvent'](),
        TYPES['NGGachaChoiceCubeInfo'](), reel)


@handler(30062)
async def join_dimension_gacha(s, a):
    """Opening the summon screen, and the free "Touch!" pull.

    actionType 0 is just opening the banner; anything else is the player
    actually pulling the free Time Cube summon.
    """
    p = s.player
    gid, action = a['openGachaID'], a['actionType']
    log.info('dimension gacha: banner=%s action=%s', gid, action)

    if gid <= 0:
        # Reopening the screen. Bin any cube the client never opened, or its
        # "previous progress was not complete" check blocks every summon from
        # here on -- see GachaSummon @0x1A3C4D0.
        p.clear_gacha_results()

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
        rolled = gacha.roll(gid, 1)
        for uid, rare in rolled:
            # The cube the client is about to open. Whatever it pays out is
            # granted here rather than at BuyDimensionGachaReq, so a player who
            # never taps through the result page still keeps what they bought.
            p.set_gacha_result(gid, uid, rare)
            unit, shards = gacha.grant(p, uid, rare)
            if unit is not None:
                added.append(unit)
            else:
                rewards.grant(p, shards)
                log.info('duplicate %s -> %s', _name(uid), shards or 'nothing')
        if rolled:
            p.bump_summon_count(gid, len(rolled))
            p.save()
            log.info('cube pull on banner %d -> %s', gid,
                     ', '.join('%s(R%d)' % (_name(u), r) for u, r in rolled))

    # _GachaChoiceCubeInfo stays zeroed.  An earlier version filled it in with
    # the banner and the hero just rolled, on the theory that the result page
    # was waiting for it.  It is not: the choice cube ("pick your own hero") is
    # an *event* feature -- DimensionGachaTabHeroUI reaches it only through
    # NMEvent.CheckChoiceCubeUpgrade @0x1A3C8AC and
    # NMUserInfo.CheckChoiceCubeSummon @0x1A3C798, both driven by the event
    # list, and HeroGachaEventInfo is where choiceCubeSeason and
    # choiceCubeSummonMaxCount live.  We serve no events, so there is no cube to
    # announce.  Zeroing it did NOT stop the client throwing, so it was not the
    # cause of that -- it is just the honest value.
    cube = TYPES['NGGachaChoiceCubeInfo']()
    # The dimension itself goes back through the sync struct. Without it the
    # client's NGDimensionGacha for this banner keeps the empty vecSummon it
    # got at login, GetGachaCount() stays 0, and the result page has no cube
    # to open -- which is what "The Dimension has dissipated. Moving onto
    # Dimension Select Page." (string 1106) is telling us.
    if gid > 0 and not RICH_ACK:
        p.take_dirty()                      # swallow the delta; send the lot instead
        p.take_dirty_collections()
        sync = state.check_info(vecAddResourceInfo=state.resource_infos(p))
        count = gacha.summons_count(p, -1)
    else:
        sync = state.resource_sync(
            p, vecAddUnitInfo=[state.unit_info(u) for u in added],
            vecChangeDimensionGacha=[gacha.dimension(p, gid)] if gid > 0 else [])
        count = gacha.summons_count(p, gid)

    if ACK_DELAY and gid > 0:
        log.info('holding the Ack back %.1fs (HC_GACHA_ACK_DELAY)', ACK_DELAY)
        await asyncio.sleep(ACK_DELAY)
    await s.send(40067, Err.OK, sync, count, TYPES['NGPairInt2'](),
                 TYPES['NGIntIntInt'](), cube)


@handler(30063)
async def buy_dimension_gacha(s, a):
    """Opening the cube a summon produced.

    The charge and the hero both happened at JoinDimensionGachaReq, because
    that is where the summon actually is; this only closes the transaction, so
    that vecSummon[0].SoldOut goes true and the next summon is allowed.
    """
    p = s.player
    gid, slot_id = a['GachaID'], a['slotID']
    taken = p.take_gacha_slot(gid, slot_id)
    if taken is None:
        log.info('cube %s on banner %s already opened', slot_id, gid)
        await s.send(40068, Err.OK,
                     state.resource_sync(p,
                                         vecChangeDimensionGacha=[gacha.dimension(p, gid)]))
        return

    p.save()
    log.info('opened cube %s on banner %s -> %s', slot_id, gid, _name(taken['unit']))
    await s.send(40068, Err.OK,
                 state.resource_sync(p,
                                     vecChangeDimensionGacha=[gacha.dimension(p, gid)]))


@handler(30064)
async def reduce_summon_timer(s, a):
    await s.send(40069, Err.OK, state.resource_sync(s.player))


@handler(30285)
async def gacha_simulation_no_update(s, a):
    await s.send(40307, Err.OK)
