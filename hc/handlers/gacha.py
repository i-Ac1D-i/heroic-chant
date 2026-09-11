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

# A summon is answered with the same Ack *shape* as a screen-open, which is the
# only shape the client does not throw on.  Anything fuller made
# JoinDimensionGachaAck raise
#     NGNetGameServer OK, Object reference not set to an instance of an object.
# inside the client (see `adb logcat | grep Unity`), which leaves
# DimensionGachaTabHeroUI.bSendPacket set -- GachaSummon @0x1A3C3E4 opens with
# `if (bSendPacket) return;` -- so the summon button dies and the screen cannot
# be dismissed.
#
# The older note here said the safe shape differed from the natural one in two
# ways.  It differs in FIVE, and they were never varied independently, which is
# why the hunt stalled.  They are the parts below.  Exactly one of them is the
# trigger; `HC_GACHA_ACK_PARTS` turns them on one at a time so a single session
# on the device settles it instead of a rebuild per guess.
#
#   wallet       vecAddResourceInfo carries only what changed, not the whole
#                wallet
#   collections  vecAddCollectionInfo carries the counters that changed
#   units        vecAddUnitInfo announces a genuinely new hero
#   dimension    vecChangeDimensionGacha carries the banner and its cube
#   banner       NGGachaDaySummonsCount.GachaID is the banner id, not -1
#
# Read off the client, and worth knowing before you test (RVAs are into
# libil2cpp.so, `python tools/disasm.py --rva ...`):
#
#   * JoinDimensionGachaAck is @0x14EBE38 on NGNetGameServer, whose signature
#     is (int Error, NGCheckServerInfo, NGGachaDaySummonsCount, NGPairInt2,
#     NGIntIntInt, NGGachaChoiceCubeInfo) -- confirmed in dump.cs, so our
#     argument order is right.
#   * `banner` cannot be the trigger.  AddGachaLimitInfo @0x13A3104 does
#     nothing with GachaID but `_dic[item.GachaID] = item`, a dictionary
#     indexer *set*.  It throws only if the item itself, or the dictionary, is
#     null -- never because of the id's value.
#   * `dimension` is the one to try first.  @0x14EC148 the handler walks
#     vecAddDimensionGacha ++ vecChangeDimensionGacha (NGCheckServerInfo
#     +0xA0 and +0xA8) and dereferences, per entry and with no skip path,
#     NGDimensionGacha.vecSummon (+0x20) and every
#     NGDimensionGachaSummon.ProductInfo (+0x20).  Each is a throw site.
#     Deeper still, AddDimensionGacha @0x137CA98 ends in
#     `_dicDimensionGacha[ngInfo.ID] = ngInfo` and throws if that static
#     (NMUserInfo +0xC0) is null -- and it is only ever reached once the list
#     has an entry, so an empty list cannot trip it.  That matches the
#     symptom exactly: empty is safe, one entry is fatal.
#   * The whole Ack, including the `_JoinDimensionGachaAck` subject's OnNext
#     at the end (+0x238), runs inside NGNetGameServer's catch.  So the throw
#     may be in a UI subscriber rather than the handler itself.
#
# Cost of the workaround: a genuinely *new* hero is not announced in
# vecAddUnitInfo, so it only appears after a relogin, and the "Dimension has
# dissipated" popup is the client noticing it never got `dimension`.  Those two
# open items are one bug.  Duplicates -- which is every pull once the roster is
# complete -- are unaffected, because the client picks the shard up by diffing
# the wallet.
ACK_PARTS = ('wallet', 'collections', 'units', 'dimension', 'banner')


def _ack_parts():
    """Which parts of the fuller Ack to send.  Empty is the safe shape.

    HC_GACHA_ACK_PARTS=dimension      just that one
    HC_GACHA_ACK_PARTS=units,banner   those two
    HC_GACHA_ACK_PARTS=all            everything -- the pre-workaround Ack
    HC_GACHA_RICH_ACK=1               kept working; same as `all`
    """
    raw = os.environ.get('HC_GACHA_ACK_PARTS', '').strip()
    if not raw and os.environ.get('HC_GACHA_RICH_ACK', '0') in ('1', 'yes', 'true'):
        raw = 'all'
    if not raw:
        return frozenset()
    if raw.lower() in ('all', '*'):
        return frozenset(ACK_PARTS)
    want = {w.strip().lower() for w in raw.replace(';', ',').split(',') if w.strip()}
    unknown = want - set(ACK_PARTS)
    if unknown:
        log.warning('ignoring unknown HC_GACHA_ACK_PARTS %s; known parts are %s',
                    ', '.join(sorted(unknown)), ', '.join(ACK_PARTS))
    return frozenset(want & set(ACK_PARTS))


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


def summon_ack_shape(p, gid, added):
    """The NGCheckServerInfo + NGGachaDaySummonsCount for JoinDimensionGachaAck.

    Split out from the handler because which fields this puts on the wire is the
    open question, and a pure function can be exercised without a device.
    ``gid <= 0`` is the screen opening, which is confirmed good and is left
    exactly as it was; ``gid > 0`` is a pull, and is where the parts apply.
    """
    if gid <= 0:
        return (state.resource_sync(p, vecAddUnitInfo=[state.unit_info(u)
                                                       for u in added],
                                    vecChangeDimensionGacha=[]),
                gacha.summons_count(p, gid))

    # A real pull.  Build the safe shape, then add back only the parts
    # HC_GACHA_ACK_PARTS asks for, so exactly one of them can be blamed.
    parts = _ack_parts()
    dirty, dirty_cols = p.take_dirty(), p.take_dirty_collections()
    extra = {}
    if 'collections' in parts and dirty_cols:
        extra['vecAddCollectionInfo'] = state.collection_infos(p, dirty_cols)
    if 'units' in parts:
        extra['vecAddUnitInfo'] = [state.unit_info(u) for u in added]
    if 'dimension' in parts:
        extra['vecChangeDimensionGacha'] = [gacha.dimension(p, gid)]
    # Without 'wallet' the delta above is swallowed and the whole wallet goes.
    only = (dirty or None) if 'wallet' in parts else None
    sync = state.check_info(vecAddResourceInfo=state.resource_infos(p, only),
                            **extra)
    if parts:
        log.info('summon Ack carrying %s (HC_GACHA_ACK_PARTS)',
                 ', '.join(sorted(parts)))
    return sync, gacha.summons_count(p, gid if 'banner' in parts else -1)


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
    sync, count = summon_ack_shape(p, gid, added)

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
