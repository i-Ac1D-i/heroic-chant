"""Unit progression: level up, tier up, grade up, equipment."""
import logging

from ..net import handler
from ..data.tables import TABLES, to_int
from ..protocol.dto import TYPES
from ..game import state, rewards, scenecards
from ..game.errors import Err

log = logging.getLogger('hc.units')


def _costs(row, pairs):
    """Pull (resourceID, type2, value) triples out of a cost table row."""
    out = []
    for rid_key, t2_key, val_key in pairs:
        rid = to_int(row.get(rid_key), -1)
        val = to_int(row.get(val_key), 0)
        if rid < 0 or val <= 0:
            continue
        out.append((rid, to_int(row.get(t2_key), -1) if t2_key else -1, val))
    return out


def _pay(player, costs):
    if any(player.get_resource(r, t2) < v for r, t2, v in costs):
        return False
    for r, t2, v in costs:
        player.add_resource(r, -v, t2)
    return True


@handler(30001)
async def unit_level_up(s, a):
    p = s.player
    u = p.find_unit(a['uid'])
    if u is None:
        await s.send(40006, Err.NOT_FOUND, state.resource_sync(p))
        return
    target = u.get('level', 1) + 1
    row = TABLES.index('LevelUpTable', 'Lv', source='sql').get(target)
    if row is None:
        log.info('unit %d is already at max level %d', u['uid'], u['level'])
        await s.send(40006, Err.INVALID, state.resource_sync(p))
        return
    costs = _costs(row, [('ResourceID_1', None, 'Val_1'),
                         ('ResourceID_2', None, 'Val_2')])
    if not _pay(p, costs):
        await s.send(40006, Err.NOT_ENOUGH, state.resource_sync(p))
        return
    u['level'] = target
    p.save()
    await s.send(40006, Err.OK, state.resource_sync(p, vecChangeUnitInfo=state.unit_infos(p, [u])))


@handler(30002)
async def unit_tier_up(s, a):
    p = s.player
    u = p.find_unit(a['uid'])
    if u is None:
        await s.send(40007, Err.NOT_FOUND, state.resource_sync(p))
        return
    element = to_int((TABLES.unit(u['id']) or {}).get('ElementID'), 1)
    target = u.get('tier', 1) + 1
    row = next((r for r in TABLES.sql('TierUpTable')
                if to_int(r['Tier']) == target and to_int(r['ElementID']) == element), None)
    if row is None:
        await s.send(40007, Err.INVALID, state.resource_sync(p))
        return
    costs = _costs(row, [('ResourceID_1', None, 'Val_1'),
                         ('ResourceID_2', None, 'Val_2'),
                         ('ResourceID_3', 'ResourceID_3_Type2', 'Val_3')])
    if not _pay(p, costs):
        await s.send(40007, Err.NOT_ENOUGH, state.resource_sync(p))
        return
    u['tier'] = target
    p.save()
    await s.send(40007, Err.OK, state.resource_sync(p, vecChangeUnitInfo=state.unit_infos(p, [u])))


def grade_up_cost(unit_id, grade):
    """GradeUpMaterial rows for one star-up step: elemental Shards, a Memory
    Stone of the right letter, and from 3 stars on the hero's own Memory."""
    return [(to_int(r['ResourceType1']), to_int(r['ResourceType2']),
             to_int(r['ResourceVal1'], 0))
            for r in TABLES.sql('GradeUpMaterial')
            if to_int(r['unitID']) == int(unit_id) and to_int(r['grade']) == int(grade)]


@handler(30003)
async def unit_grade_up(s, a):
    """Star-up.  Costs come straight from GradeUpMaterial, keyed by the unit's
    *current* grade -- grade N is the row that takes it to N+1."""
    p = s.player
    u = p.find_unit(a['uid'])
    if u is None:
        await s.send(40008, Err.NOT_FOUND, state.resource_sync(p))
        return

    grade = u.get('grade', 1)
    costs = grade_up_cost(u['id'], grade)
    if not costs:
        log.info('unit %d is already at max grade (%d)', u['uid'], grade)
        await s.send(40008, Err.INVALID, state.resource_sync(p))
        return
    if not _pay(p, costs):
        log.info('star-up refused for unit %d: needs %s', u['uid'], costs)
        await s.send(40008, Err.NOT_ENOUGH, state.resource_sync(p))
        return

    u['grade'] = grade + 1
    # Fodder units named by the client are consumed.
    removed = []
    for uid in list(a.get('vecMaterialUnitID') or []):
        victim = p.find_unit(uid)
        if victim is not None and victim is not u:
            p.d['units'].remove(victim)
            removed.append(state.unit_info(victim))
    p.save()
    log.info('unit %d star-up %d -> %d (paid %s)', u['uid'], grade, u['grade'], costs)
    await s.send(40008, Err.OK, state.resource_sync(
        p, vecChangeUnitInfo=state.unit_infos(p, [u]), vecDelUnitInfo=removed))


def awaken_cost(unit_id, parts_id):
    return [(to_int(r['resourceType1']), to_int(r['resourceType2']),
             to_int(r['resourceValue1'], 0))
            for r in TABLES.sql('unitAwakenOpenCost')
            if to_int(r['unitID']) == int(unit_id)
            and to_int(r['partsInfoID']) == int(parts_id)]


def awaken_node_exists(unit_id, parts_id):
    return any(to_int(r['unitID']) == int(unit_id)
               and to_int(r['partsInfoID']) == int(parts_id)
               for r in TABLES.sql('unitAwakenPartsInfo'))


@handler(30004)
async def unit_awaken(s, a):
    """Awakening -- the per-hero skill tree.  Each node is a partsInfoID with
    its own cost in unitAwakenOpenCost (Shards, hero Memory, Fame Point).
    The client sends the nodes it wants opened; unlocked ids are stored on the
    unit and replayed in NGUnitInfo.vecAwakenInfo."""
    p = s.player
    u = p.find_unit(a['uid'])
    if u is None:
        await s.send(40009, Err.NOT_FOUND, state.resource_sync(p))
        return

    opened = u.setdefault('awaken', [])
    new_cards = []
    wanted = [int(x) for x in (a.get('vecPartsInfoID') or [])]
    for parts_id in wanted:
        if parts_id in opened:
            continue
        if not awaken_node_exists(u['id'], parts_id):
            log.warning('unit %d has no awaken node %d', u['id'], parts_id)
            continue
        costs = awaken_cost(u['id'], parts_id)
        if not _pay(p, costs):
            log.info('awaken refused for unit %d node %d: needs %s',
                     u['uid'], parts_id, costs)
            await s.send(40009, Err.NOT_ENOUGH, state.resource_sync(p))
            return
        opened.append(parts_id)
        log.info('unit %d awakened node %d (paid %s)', u['uid'], parts_id, costs)

        # Some nodes hand over the hero's own Relic.  `unitAwakenBonusReward`
        # is the table, and the client's own tree node reads exactly it --
        # AwakenRewardRelic.UpdateUI @0x1BC2078 calls
        # NMUnit.GetUnitAwakenBonusReward(unitID, partsInfoID) and renders the
        # result.  Every shipped row pays ResourceType 42 (SceneCard), which is
        # what the UI calls a Relic, at node 1002.  Without this the node showed
        # a reward and gave nothing.
        bonus = awaken_bonus_reward(u['id'], parts_id)
        for card_id, count in bonus:
            for _ in range(max(1, count)):
                relic = p.add_scenecard(card_id)
                new_cards.append(relic)
            log.info('unit %d node %d granted relic %d x%d',
                     u['uid'], parts_id, card_id, max(1, count))

    await s.send(40009, Err.OK, state.resource_sync(
        p, vecChangeUnitInfo=state.unit_infos(p, [u]),
        vecAddSceneCard=[scenecards.info(c) for c in new_cards]))


def awaken_bonus_reward(unit_id, parts_id):
    """[(scene card id, count)] this awakening node hands over.

    `unitAwakenBonusReward` keyed by (unitID, partsInfoID).  All 32 shipped
    rows are ResourceType 42 (SceneCard, "Relic" in the UI) at node 1002, one
    per hero -- that hero's own Relic.  Anything that is not a Scene Card is
    skipped rather than guessed at, because no such row exists to test with.
    """
    out = []
    for r in TABLES.sql('unitAwakenBonusReward'):
        if (to_int(r.get('unitID'), -1) != int(unit_id)
                or to_int(r.get('partsInfoID'), -1) != int(parts_id)):
            continue
        if to_int(r.get('resourceType1'), -1) != scenecards.SCENE_CARD:
            log.warning('awaken bonus for unit %s node %s is resource type %s,'
                        ' which is not handled', unit_id, parts_id,
                        r.get('resourceType1'))
            continue
        out.append((to_int(r.get('resourceType2'), -1),
                    to_int(r.get('resourceValue1'), 1)))
    return [(cid, n) for cid, n in out if cid > 0]


def awaken_role_stats(unit_id):
    """`unitAwakenRoleStat` rows for one hero, by node count needed.

    `checkOpenUnit` is the hero.  Each row is one Awakening Passive Mastery
    stat: `openAwakenCnt` nodes open it, and `awakenRoleStatID` is what
    `rarenessInfo.reqAwakenRoleStat` points at.
    """
    rows = [r for r in TABLES.sql('unitAwakenRoleStat')
            if to_int(r.get('checkOpenUnit'), -1) == int(unit_id)]
    return sorted(rows, key=lambda r: to_int(r.get('openAwakenCnt'), 0))


def awaken_stat_row(stat_id):
    for r in TABLES.sql('unitAwakenRoleStat'):
        if to_int(r.get('awakenRoleStatID'), -1) == int(stat_id):
            return r
    return None


@handler(30228)
async def awaken_stat_open(s, a):
    """Claim an Awakening Passive Mastery stat.

    This is the "passive slot" on the awakening screen, and it had no handler,
    so the request decoded, logged and vanished -- the claim button did
    nothing.  That also kept rank-up locked: `rarenessInfo.reqAwakenRoleStat`
    names one of these stats, so a hero whose stat is never claimed can never
    be promoted no matter how many stars it has.

    The Ack carries no NGCheckServerInfo, and `unitAwakenRoleStat` has no cost
    columns, so claiming is free -- it is a claim, not a purchase.  The whole
    claimed set goes back every time, which is also what NGLogInAck03 replays.
    """
    p = s.player
    stat_id = int(a['_AwakenStatID'])
    row = awaken_stat_row(stat_id)
    if row is None:
        log.info('no such awaken stat: %d', stat_id)
        await s.send(40251, Err.NOT_FOUND, stat_id, _awaken_stats(p))
        return

    unit_id = to_int(row.get('checkOpenUnit'), -1)
    need = to_int(row.get('openAwakenCnt'), 0)
    best = max([len(u.get('awaken') or []) for u in p.d['units']
                if int(u['id']) == unit_id] or [0])
    if best < need:
        log.info('awaken stat %d needs %d node(s) on unit %d, best owned has %d',
                 stat_id, need, unit_id, best)
        await s.send(40251, Err.INVALID, stat_id, _awaken_stats(p))
        return

    if p.open_awaken_stat(stat_id):
        p.save()
        log.info('awaken stat %d claimed (unit %d, %d nodes)',
                 stat_id, unit_id, need)
    else:
        log.info('awaken stat %d was already claimed', stat_id)
    await s.send(40251, Err.OK, stat_id, _awaken_stats(p))


def _awaken_stats(p):
    return [TYPES['NGAwakenStat'](AwakenStatID=int(sid), State=1)
            for sid in p.awaken_stats()]


@handler(30093)
async def unit_awaken_state_change(s, a):
    """Toggle one awakening node on or off.

    `unitAwakenPartsInfo.radioBoxGroup` puts some nodes in mutually exclusive
    groups -- picking one turns its sibling off -- and `isRecall` marks a node
    that can be switched back off at all.  Nodes outside a group (every node on
    the heroes checked, where radioBoxGroup is -1) simply toggle.

    Unhandled before, so turning a passive on or off silently did nothing.
    """
    p = s.player
    u = p.find_unit(a['uid'])
    if u is None:
        await s.send(40104, Err.NOT_FOUND, state.resource_sync(p))
        return

    parts_id = int(a['partsInfoID'])
    if not awaken_node_exists(u['id'], parts_id):
        log.warning('unit %d has no awaken node %d', u['id'], parts_id)
        await s.send(40104, Err.INVALID, state.resource_sync(p))
        return

    opened = u.setdefault('awaken', [])
    off = u.setdefault('awaken_off', [])
    if parts_id in opened:
        # Turning it off.  Only a node the table says is recallable.
        row = _parts_row(u['id'], parts_id)
        if row is not None and to_int(row.get('isRecall'), 0) != 1:
            log.info('awaken node %d on unit %d cannot be switched off',
                     parts_id, u['uid'])
            await s.send(40104, Err.INVALID, state.resource_sync(p))
            return
        opened.remove(parts_id)
        if parts_id not in off:
            off.append(parts_id)
        log.info('unit %d switched awaken node %d off', u['uid'], parts_id)
    else:
        # Turning it back on.  Siblings in the same radio group go off.
        group = _radio_group(u['id'], parts_id)
        if group >= 0:
            for sibling in list(opened):
                if _radio_group(u['id'], sibling) == group:
                    opened.remove(sibling)
                    if sibling not in off:
                        off.append(sibling)
                    log.info('unit %d radio group %d: node %d gave way',
                             u['uid'], group, sibling)
        opened.append(parts_id)
        if parts_id in off:
            off.remove(parts_id)
        log.info('unit %d switched awaken node %d on', u['uid'], parts_id)

    p.save()
    await s.send(40104, Err.OK, state.resource_sync(
        p, vecChangeUnitInfo=state.unit_infos(p, [u])))


def _parts_row(unit_id, parts_id):
    for r in TABLES.sql('unitAwakenPartsInfo'):
        if (to_int(r.get('unitID'), -1) == int(unit_id)
                and to_int(r.get('partsInfoID'), -1) == int(parts_id)):
            return r
    return None


def _radio_group(unit_id, parts_id):
    row = _parts_row(unit_id, parts_id)
    return to_int(row.get('radioBoxGroup'), -1) if row else -1


def awaken_reward_group(unit_id):
    """UnitList.awakenRewardGroup -- which reward ladder this hero uses."""
    row = TABLES.unit(unit_id) or {}
    return to_int(row.get('awakenRewardGroup'), -1)


def awaken_reward_rows(unit_id):
    """The hero's reward ladder, in ascending rewardIndex order.

    `unitAwakenReward` is keyed by awakenRewardGroup, and each row carries the
    `conditionValue` -- how many awakening nodes must be open -- plus up to
    three resources.  Group 1 wants 5/10/15/20 nodes, group 2 wants 4/8/12/16;
    the hero's own group decides which.
    """
    group = awaken_reward_group(unit_id)
    if group < 0:
        return []
    rows = [r for r in TABLES.sql('unitAwakenReward')
            if to_int(r.get('awakenRewardGroup'), -1) == group]
    return sorted(rows, key=lambda r: to_int(r.get('rewardIndex'), 0))


def awaken_reward_payout(row):
    """What one rung of the ladder pays, as reward tuples.

    `resourceType1/2/3` are the (Type1, Type2, Type3) of a *single* resource --
    the same shape `GradeUpMaterial` uses -- not three separate ones.  All eight
    shipped rows are Gold (type 0) at 1k / 10k / 100k / 1M, with
    `resourceValue2` and `resourceValue3` zero throughout, which is what
    NGResourceInfo's unused Value2/Value3 look like.
    """
    val = to_int(row.get('resourceValue1'), 0)
    t1 = to_int(row.get('resourceType1'), -1)
    if t1 < 0 or val <= 0:
        return []
    return [(t1, to_int(row.get('resourceType2'), -1),
             to_int(row.get('resourceType3'), -1), val)]


@handler(30101)
async def unit_awaken_reward(s, a):
    """Claim the awakening passives -- the milestone rewards on the tree.

    `NGUnitInfo.AwakenRewardGrade` is how many rungs of the ladder the hero has
    already claimed, and the client sends this the moment it thinks another one
    is due.  With no handler the request was decoded, logged and dropped, so the
    Claim button did nothing at all once enough nodes were open.
    """
    p = s.player
    u = p.find_unit(a['uid'])
    if u is None:
        await s.send(40113, Err.NOT_FOUND, state.resource_sync(p))
        return

    opened = len(u.get('awaken') or [])
    claimed = int(u.get('awaken_grade', 0))
    rows = awaken_reward_rows(u['id'])
    if claimed >= len(rows):
        log.info('unit %d has claimed every awakening reward (%d)',
                 u['uid'], claimed)
        await s.send(40113, Err.INVALID, state.resource_sync(p))
        return

    # Claim every rung the hero has earned, not just the next one -- a player
    # who opened several nodes before we had a handler is owed all of them.
    payout, taken = [], 0
    for row in rows[claimed:]:
        need = to_int(row.get('conditionValue'), 0)
        if opened < need:
            break
        payout.extend(awaken_reward_payout(row))
        taken += 1

    if not taken:
        nxt = to_int(rows[claimed].get('conditionValue'), 0)
        log.info('unit %d has %d awakening node(s), needs %d for reward %d',
                 u['uid'], opened, nxt, claimed + 1)
        await s.send(40113, Err.INVALID, state.resource_sync(p))
        return

    rewards.grant(p, payout)
    u['awaken_grade'] = claimed + taken
    p.save()
    log.info('unit %d claimed awakening reward %d..%d (%d nodes) -> %s',
             u['uid'], claimed + 1, u['awaken_grade'], opened, payout)
    await s.send(40113, Err.OK, state.resource_sync(
        p, vecChangeUnitInfo=state.unit_infos(p, [u])))


def rareness_row(unit_id, rareness):
    """The `rarenessInfo` step that takes this hero out of `rareness`."""
    for r in TABLES.sql('rarenessInfo'):
        if (to_int(r.get('unitID'), -1) == int(unit_id)
                and to_int(r.get('currentRareness'), -1) == int(rareness)):
            return r
    return None


def rareness_required_nodes(row):
    """How many awakening nodes `reqAwakenRoleStat` demands, or 0 if unknown.

    `rarenessInfo.reqAwakenRoleStat` points at `unitAwakenRoleStat`, whose
    `openAwakenCnt` is the real gate -- 11 nodes for the first step, 20 for the
    second, 29 for the third on the heroes checked.  It is a *role stat*, not a
    star count, which is why ranking up does not follow from starring up.
    """
    want = to_int(row.get('reqAwakenRoleStat'), -1)
    if want < 0:
        return 0
    for r in TABLES.sql('unitAwakenRoleStat'):
        if to_int(r.get('awakenRoleStatID'), -1) == want:
            return to_int(r.get('openAwakenCnt'), 0)
    return 0


def rareness_cost(row):
    """`resource_N_Type1/Type2/Value1` for N in 1..4."""
    out = []
    for n in (1, 2, 3, 4):
        t1 = to_int(row.get('resource_%d_Type1' % n), -1)
        val = to_int(row.get('resource_%d_Value1' % n), 0)
        if t1 < 0 or val <= 0:
            continue
        out.append((t1, to_int(row.get('resource_%d_Type2' % n), -1), val))
    return out


@handler(30232)
async def unit_rareness_upgrade(s, a):
    """Rank-up: A -> S -> SS -> SSS.

    `NGUnitInfo.currentRareness` is the letter on the card, and `rarenessInfo`
    is the whole rule: one row per (unitID, currentRareness) carrying four
    costs, the `resultRareness` it produces, and a `reqAwakenRoleStat` gate.
    Unhandled until now, so the Rank Up button did nothing.
    """
    p = s.player
    u = p.find_unit(a['_UnitUID'])
    if u is None:
        await s.send(40255, Err.NOT_FOUND, state.resource_sync(p))
        return

    current = int(u.get('rareness', 0))
    row = rareness_row(u['id'], current)
    if row is None:
        log.info('unit %d cannot rank up past rareness %d', u['uid'], current)
        await s.send(40255, Err.INVALID, state.resource_sync(p))
        return

    # The gate is the Awakening Passive Mastery stat, not the star count and
    # not the raw node count.  `rarenessInfo.reqAwakenRoleStat` names a
    # `unitAwakenRoleStat` row; that stat has to have been *claimed* through
    # AwakenStatOpenReq (30228).  Session 5 checked only the node count, which
    # is the condition for the stat becoming claimable -- not the same thing,
    # and with 30228 unhandled the stat could never be claimed at all, so
    # rank-up stayed locked however many stars a hero had.
    want_stat = to_int(row.get('reqAwakenRoleStat'), -1)
    if want_stat >= 0 and want_stat not in p.awaken_stats():
        need_nodes = rareness_required_nodes(row)
        opened = len(u.get('awaken') or [])
        log.info('unit %d cannot rank up: awaken stat %d is not claimed '
                 '(needs %d node(s), has %d)',
                 u['uid'], want_stat, need_nodes, opened)
        await s.send(40255, Err.INVALID, state.resource_sync(p))
        return

    costs = rareness_cost(row)
    if not _pay(p, costs):
        log.info('rank-up refused for unit %d: needs %s', u['uid'], costs)
        await s.send(40255, Err.NOT_ENOUGH, state.resource_sync(p))
        return

    u['rareness'] = to_int(row.get('resultRareness'), current + 1)
    p.save()
    log.info('unit %d rank-up rareness %d -> %d (paid %s)',
             u['uid'], current, u['rareness'], costs)
    await s.send(40255, Err.OK, state.resource_sync(
        p, vecChangeUnitInfo=state.unit_infos(p, [u])))


# 30007 UnitEquipInfoChangeReq now lives in handlers/equipment.py


@handler(30094)
async def change_party(s, a):
    """Save one party line-up.

    This is the generic one, used by every screen that has a team: the arena
    attack party arrives here as _PartyType 6, guild wars as its own type, and
    so on.  Parties all share `player.d['party']`, told apart by
    NGPartyInfo.SlotType, because that is how the client wants them back --
    NGLoginAckLargeData.vecPartyInfo is a single list with mixed SlotTypes.

    Without this the arena screen let you pick an attack team, threw the
    selection away, and then would not start a fight.
    """
    p = s.player
    rows = [{'slot_index': u.SlotIndex, 'unit_uid': u.UnitUID,
             'skill_on_off': u.SkillOnOff} for u in a['_vecPartyInfo']]
    p.set_party(a['_PartyType'], rows)
    p.save()
    log.info('party type %d set to %d unit(s)', a['_PartyType'], len(rows))
    await s.send(40105, Err.OK, a['_vecPartyInfo'])
