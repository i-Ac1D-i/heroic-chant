"""Hero progression and relics, driven through the real handlers.

    python tools/test_progression.py

Covers the four things that were reported broken and could not be checked
without a device before:

* hero Memories exist at all, so star-up can pass 3 stars
* the awakening reward ladder can be claimed
* rank-up (A -> S -> SS -> SSS) works and is gated the way the tables say
* relics can be owned, equipped, taken off and levelled

Everything runs in a sandbox directory against encoded packets, so a DTO that
does not marshal fails here rather than on the phone.
"""
import asyncio
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s  %s%s' % ('PASS' if cond else 'FAIL', name,
                          '' if cond else ('  -- ' + str(detail))))


class FakeSession(object):
    """Enough of hc.net.Session for a handler, and it really encodes."""

    def __init__(self, player):
        self.player = player
        self.account = player.account_id
        self.sent = []

    async def send(self, packet_id, *args):
        from hc.protocol.dto import encode_packet, decode_packet
        body = encode_packet(packet_id, *args)
        decoded = decode_packet(packet_id, body)
        assert not decoded.get('_trailing'), (
            'packet %d left %d trailing bytes' % (packet_id, decoded['_trailing']))
        self.sent.append((packet_id, decoded, len(body)))
        return decoded

    def last(self):
        return self.sent[-1][1] if self.sent else None


def call(session, packet_id, **args):
    from hc.net import HANDLERS
    args.setdefault('_name', 'test')
    asyncio.run(HANDLERS[packet_id](session, args))
    return session.last()


def main():
    sandbox = tempfile.mkdtemp(prefix='hc-progress-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    accounts = os.path.join(sandbox, 'accounts')
    os.makedirs(accounts)

    from hc.game import player as player_mod
    player_mod.ACCOUNTS_DIR = accounts
    from hc.game.player import Player
    from hc.game import artifacts, arena, state, scenecards
    from hc.data.tables import TABLES, to_int
    from hc.protocol.dto import TYPES
    import hc.handlers                                    # registers handlers
    from hc.handlers.units import (awaken_reward_rows, rareness_row,
                                   rareness_required_nodes, grade_up_cost)

    try:
        p = Player.create(900100, 'test-device-progression')
        s = FakeSession(p)
        u = p.d['units'][0]

        print('\nhero Memories')
        # "Q" was the hero reported as having none.  Nothing in this server
        # ever paid out ResourceType 16 before, so no hero had any.
        q = next((to_int(r['UnitID']) for r in TABLES.sql('UnitList')
                  if (TABLES.strings().get(to_int(r.get('NameID'))) or '') == 'Q'),
                 None)
        check('the hero called Q is in UnitList', q is not None, q)
        check("Q's Memory is a nameable resource",
              TABLES.resource_name(16, q).endswith("Memory"),
              TABLES.resource_name(16, q))
        check('a new account owns some of them', p.get_resource(16, q) > 0,
              p.get_resource(16, q))
        # Star-up from grade 2 charges the hero's own Memory.  Enough for the
        # whole ladder, or the report comes straight back.
        need = sum(v for t1, _t2, v in
                   sum((grade_up_cost(q, g) for g in range(2, 6)), [])
                   if t1 == 16)
        check('there are enough to go from 3 stars to 6',
              p.get_resource(16, q) >= need,
              '%d owned, %d needed' % (p.get_resource(16, q), need))

        print('\nthe dashboard can find them')
        from hc.webui.server import _catalogue
        names = [r['name'] for r in _catalogue('q', limit=25)]
        check('searching "q" offers Q\'s Memory', "Q's Memory" in names,
              names[:6])
        keys = [r['key'] for r in _catalogue('16:%d' % q, limit=5)]
        check('searching by key finds it', ('16:%d:-1' % q) in keys, keys)

        print('\nawakening rewards')
        rows = awaken_reward_rows(u['id'])
        check('the hero has a reward ladder', len(rows) > 0, len(rows))
        d = call(s, 30101, uid=u['uid'])
        check('claiming with no nodes open is refused', d['Error'] != 0)
        first = to_int(rows[0].get('conditionValue'), 0)
        u['awaken'] = list(range(1, first + 1))
        gold = p.get_resource(0)
        d = call(s, 30101, uid=u['uid'])
        check('claiming at the first threshold pays out', d['Error'] == 0,
              d['Error'])
        check('the gold arrived', p.get_resource(0) > gold,
              '%d -> %d' % (gold, p.get_resource(0)))
        check('AwakenRewardGrade went up', u.get('awaken_grade') == 1,
              u.get('awaken_grade'))
        d = call(s, 30101, uid=u['uid'])
        check('claiming the same rung twice is refused', d['Error'] != 0)
        check('and it did not bump the grade again', u['awaken_grade'] == 1)
        # Opening enough nodes for several rungs at once must pay all of them:
        # a player who awakened before there was a handler is owed the lot.
        u['awaken'] = list(range(1, to_int(rows[-1].get('conditionValue'), 0) + 1))
        call(s, 30101, uid=u['uid'])
        check('a backlog of rungs is paid in one go',
              u['awaken_grade'] == len(rows), u['awaken_grade'])

        print('\nrank-up (A -> S -> SS -> SSS)')
        start = int(u.get('rareness', 0))
        row = rareness_row(u['id'], start)
        check('the hero has a rarenessInfo step', row is not None)
        nodes = rareness_required_nodes(row)
        check('the gate is a node count, not a star count', nodes > 0, nodes)
        stat = to_int(row.get('reqAwakenRoleStat'), -1)
        check('and it is reached through an Awakening Passive Mastery stat',
              stat > 0, stat)

        u['awaken'] = list(range(1, nodes))            # one short
        d = call(s, 30232, _UnitUID=u['uid'])
        check('one node short is refused', d['Error'] != 0)
        check('and the rarity did not move', u.get('rareness', 0) == start)

        # The real gate: the stat has to be *claimed*, not merely earned.
        # AwakenStatOpenReq had no handler, which is why rank-up stayed locked.
        d = call(s, 30228, _AwakenStatID=stat)
        check('claiming the stat one node short is refused', d['Error'] != 0)
        u['awaken'] = list(range(1, nodes + 1))
        d = call(s, 30232, _UnitUID=u['uid'])
        check('enough nodes but an unclaimed stat is still refused',
              d['Error'] != 0, d['Error'])
        d = call(s, 30228, _AwakenStatID=stat)
        check('claiming the stat at the gate works', d['Error'] == 0, d['Error'])
        check('the stat is remembered', stat in p.awaken_stats(),
              p.awaken_stats())
        check('and it is replayed to the client',
              any(x.AwakenStatID == stat for x in d['_vecAwakenStat']))

        d = call(s, 30232, _UnitUID=u['uid'])
        check('with the stat claimed, rank-up goes through', d['Error'] == 0,
              d['Error'])
        check('the rarity went up', u['rareness'] == to_int(row['resultRareness']),
              u['rareness'])
        d = call(s, 30232, _UnitUID=999999)
        check('an unknown unit is refused', d['Error'] != 0)

        print('\nrelics')
        check('a new account owns relics', len(p.artifacts()) > 0,
              len(p.artifacts()))
        relic = p.artifacts()[0]
        slot = artifacts.slot_of(relic['id'])
        check('a relic goes in an EItemType artifact slot',
              slot in artifacts.SLOTS, slot)
        check('relics are sent at login',
              len(artifacts.infos(p)) == len(p.artifacts()))

        def equip(unit, key):
            return call(s, 30007, vecChangeInfo=[TYPES['NGUnitEquipInfo'](
                UnitUID=unit['uid'], ItemType=slot, ItemKey=key)])

        d = equip(u, relic['uid'])
        check('equipping a relic is accepted', d['Error'] == 0, d['Error'])
        check('the relic knows who wears it', relic['equip'] == u['uid'],
              relic['equip'])
        check('the hero knows what is in the slot',
              u['equip'].get(str(slot)) == relic['uid'])
        check('the Ack tells the client the relic moved',
              len(d['_CheckInfo'].vecChangeArtifactInfo or []) == 1)
        # The relic must ride on the unit as well, or an opponent's client has
        # no way to render a relic it does not own.
        worn = state.unit_infos(p, [u])[0]
        check('the relic rides on the unit itself',
              len(worn.vecArtifactInfo or []) == 1)
        check('and it is the right one',
              worn.vecArtifactInfo[0].UID == relic['uid'])

        other = p.d['units'][1]
        equip(other, relic['uid'])
        check('moving it to another hero takes it off the first',
              u['equip'].get(str(slot)) is None
              and other['equip'].get(str(slot)) == relic['uid'])
        check('and the relic follows', relic['equip'] == other['uid'])
        equip(other, 0)
        check('emptying the slot unequips it',
              relic['equip'] == 0 and other['equip'].get(str(slot)) is None)

        d = equip(u, 99999999)
        check('a relic the player does not own is refused',
              u['equip'].get(str(slot)) is None)

        print('\nrelic levelling')
        target = p.artifacts()[0]
        fodder = [x['uid'] for x in p.artifacts()[1:3]]
        owned = len(p.artifacts())
        d = call(s, 30014, uidArtifact=target['uid'], vecMaterialArtifact=fodder)
        check('feeding relics is accepted', d['Error'] == 0, d['Error'])
        check('the target gained MaterialEXP', target['exp'] > 0, target['exp'])
        check('the fodder is gone', len(p.artifacts()) == owned - 2,
              len(p.artifacts()))
        # A worn relic must not be eaten -- the client has its own error for
        # this (Error_MaterialArtifactVaildEquipUnitUID, 1159).
        keep = p.artifacts()[1]
        equip(u, keep['uid'])
        owned = len(p.artifacts())
        call(s, 30014, uidArtifact=target['uid'], vecMaterialArtifact=[keep['uid']])
        check('a worn relic is not eaten', len(p.artifacts()) == owned,
              len(p.artifacts()))

        print('\narena bots carry the lot')
        rid = artifacts.catalogue(grades=(5,))[0]
        nodes_for_1 = len(arena.awaken_nodes(1))
        clean, problems = arena.check_bot({'name': 'Relic Guard', 'units': [
            {'id': 1, 'level': 110, 'tier': 10, 'grade': 6, 'rareness': 3,
             'equip': [52, 552, 1052, 1552], 'awaken_count': 10,
             'artifacts': [rid]}]})
        member = clean['units'][0]
        check('a bot hero can be awakened', len(member['awaken']) == 10,
              len(member['awaken']))
        check('a bot hero can wear gear', member['equip'] == [52, 552, 1052, 1552])
        check('a bot hero can wear an artifact', member['artifacts'] == [rid],
              member['artifacts'])
        check('the team is otherwise clean', not problems, problems)

        bad, problems = arena.check_bot({'name': 'Bad', 'units': [
            {'id': 1, 'level': 30, 'tier': 1, 'awaken': [999999],
             'artifacts': [50001]}]})
        check('an awakening node the hero lacks is dropped',
              bad['units'][0]['awaken'] == [], bad['units'][0]['awaken'])
        check('a material artifact is not wearable',
              bad['units'][0]['artifacts'] == [], bad['units'][0]['artifacts'])
        check('and both were explained', len(problems) >= 2, problems)

        capped, _ = arena.check_bot({'name': 'Capped', 'units': [
            {'id': 1, 'level': 30, 'tier': 1, 'awaken_count': 99999}]})
        check('awaken_count is clamped to the hero\'s own tree',
              len(capped['units'][0]['awaken']) == nodes_for_1,
              len(capped['units'][0]['awaken']))

        two = artifacts.catalogue(grades=(5,))[:2]
        one_slot, _ = arena.check_bot({'name': 'Two', 'units': [
            {'id': 1, 'level': 30, 'tier': 1, 'artifacts': two}]})
        check('only one artifact fits a slot',
              len(one_slot['units'][0]['artifacts']) == 1,
              one_slot['units'][0]['artifacts'])

        print('\nthe opponent the client actually receives')
        from hc.settings import SETTINGS
        SETTINGS.set('arena.bots', [{'name': 'Relic Guard', 'points': 1500,
                                     'profile': 1, 'exp': 1000,
                                     'units': [{'id': 1, 'level': 110,
                                                'tier': 10, 'grade': 6,
                                                'rareness': 3,
                                                'equip': [52, 552, 1052, 1552],
                                                'awaken_count': 10,
                                                'artifacts': [rid]}]}])
        bot = arena._custom_bots()[0]
        mu = arena.match_user({'account_id': bot['index'], 'bot_index': 0,
                               'slot': 0, 'name': bot['name'],
                               'points': bot['points'], 'exp': bot['exp'],
                               'profile': bot['profile']})
        info = mu.vecUnitInfo[0]
        check('the opponent is awakened', len(info.vecAwakenInfo or []) == 10)
        equips = {e.ItemType: e.ItemKey for e in (info.vecEquipInfo or [])}
        check('the opponent wears gear in slots 1-4',
              all(equips.get(n, 0) > 0 for n in (1, 2, 3, 4)), equips)
        check('the opponent wears the artifact in its slot',
              equips.get(slot, 0) > 0, equips)
        check('and the artifact data travels with the unit',
              len(info.vecArtifactInfo or []) == 1)
        check('the artifact says it is worn by that unit',
              info.vecArtifactInfo[0].EquipUnitUID == info.UID)
        check('the key matches the artifact UID',
              equips.get(slot) == info.vecArtifactInfo[0].UID)
        # The slot rule the handoff is loud about: anything but an
        # EUnitPosition black-screens the battle scene.
        check('every defender stands at a real EUnitPosition',
              all(pi.SlotIndex in arena.DEFAULT_SLOTS
                  for pi in mu.vceUnitPartyInfo),
              [pi.SlotIndex for pi in mu.vceUnitPartyInfo])
        print("\nthe awakening tree's own Relic")
        # `unitAwakenBonusReward` pays a Scene Card -- which the UI calls a
        # Relic -- at node 1002, for the 32 heroes that have one.  The tree
        # node showed it and the server never granted it.
        from hc.handlers.units import awaken_bonus_reward
        hero21 = next((x for x in p.d['units'] if int(x['id']) == 21), None)
        check('a hero with a tree relic is in the roster', hero21 is not None)
        bonus = awaken_bonus_reward(21, 1002)
        check('the table says node 1002 pays one relic', len(bonus) == 1, bonus)
        before = len(p.scenecards())
        d = call(s, 30004, uid=hero21['uid'], vecPartsInfoID=[1002])
        check('opening the node works', d['Error'] == 0, d['Error'])
        check('and it hands over the relic',
              len(p.scenecards()) == before + 1, len(p.scenecards()))
        check('the relic is the one the table names',
              p.scenecards()[-1]['id'] == bonus[0][0], p.scenecards()[-1]['id'])
        check('the client is told about it',
              len(d['_CheckInfo'].vecAddSceneCard or []) == 1)

        print('\nrelics are Scene Cards, not Artifacts')
        check('they are a different resource type',
              scenecards.SCENE_CARD != artifacts.ARTIFACT,
              (scenecards.SCENE_CARD, artifacts.ARTIFACT))
        check('and different equip slots',
              not set(scenecards.SLOTS) & set(artifacts.SLOTS),
              (scenecards.SLOTS, artifacts.SLOTS))
        check('relics are sent at login',
              len(scenecards.infos(p)) == len(p.scenecards()))

        relic = p.scenecards()[-1]
        rslot = scenecards.SLOTS[0]

        def wear(unit, key, slot=rslot):
            return call(s, 30007, vecChangeInfo=[TYPES['NGUnitEquipInfo'](
                UnitUID=unit['uid'], ItemType=slot, ItemKey=key)])

        d = wear(hero21, relic['uid'])
        check('a relic can be equipped', d['Error'] == 0, d['Error'])
        check('the relic knows its wearer and slot',
              relic['equip'] == hero21['uid'] and relic['slot'] == rslot)
        check('the Ack reports the change',
              len(d['_CheckInfo'].vecChangeSceneCard or []) == 1)
        worn = state.unit_infos(p, [hero21])[0]
        check('and it rides on the unit for opponents to see',
              len(worn.vecSceneCardInfo or []) == 1)
        wear(hero21, 0)
        check('emptying the slot unequips it', relic['equip'] == 0)

        print('\nthe Forge')
        check('slot 1 is free', p.forge_slot_open(scenecards.free_slot()))
        check('slot 2 is not', not p.forge_slot_open(2))
        d = call(s, 30154, _SlotIndex=2)
        check('opening slot 2 works', d['Error'] == 0, d['Error'])
        check('and it stays open', p.forge_slot_open(2))
        d = call(s, 30154, _SlotIndex=99)
        check('a slot that does not exist is refused', d['Error'] != 0)

        check('a new account has Craft KIT to spend',
              p.get_resource(scenecards.CRAFT_KIT) > 0,
              p.get_resource(scenecards.CRAFT_KIT))

        # Crafting with a hero who has a fixed reward at the rolled grade
        # always yields that card -- every sceneCardCreateUnitBonus row is
        # Fixed_Reward = 1.  Unit 24 at grade 4 is card 94002.
        check('the fixed-reward table resolves',
              scenecards.unit_bonus(24, 4) is not None,
              scenecards.unit_bonus(24, 4))
        check('a fixed-reward card is held out of the random pool',
              scenecards.unit_bonus(24, 4) not in scenecards.random_pool(4))
        check('so is an awakening-only relic',
              bonus[0][0] not in scenecards.random_pool(5))

        top = scenecards.create_levels()[-1]
        pieces = to_int(scenecards.create_cost(top).get('UnitPieceCount'), 0)
        p.add_resource(scenecards.MEMORY, pieces * 2, 24)
        kit_before = p.get_resource(scenecards.CRAFT_KIT)
        mem_before = p.get_resource(scenecards.MEMORY, 24)
        cost = TYPES['NGResourceInfo'](Type1=scenecards.MEMORY, Type2=24,
                                       Type3=-1, Value1=pieces, Value2=0,
                                       Value3=0)
        d = call(s, 30155, _SlotIndex=1, _UnitPieceCost=cost)
        check('a craft starts', d['Error'] == 0, d['Error'])
        check('it charges the hero Memory',
              p.get_resource(scenecards.MEMORY, 24) == mem_before - pieces)
        check('and the Craft KIT',
              p.get_resource(scenecards.CRAFT_KIT) < kit_before)
        slot = d['_info']
        check('the slot names what is cooking', slot.ResultSceneCardID > 0,
              slot.ResultSceneCardID)
        check('and it is a real card',
              scenecards.exists(slot.ResultSceneCardID))
        d = call(s, 30155, _SlotIndex=1, _UnitPieceCost=cost)
        check('a busy slot refuses a second craft', d['Error'] != 0)

        owned = len(p.scenecards())
        d = call(s, 30157, _SlotIndex=1)
        check('the craft can be collected', d['Error'] == 0, d['Error'])
        check('and the relic arrives', len(p.scenecards()) == owned + 1)
        check('the client is told', len(d['_CheckInfo'].vecAddSceneCard or []) == 1)
        check('the slot is empty again', d['_info'].ResultSceneCardID == 0)
        d = call(s, 30157, _SlotIndex=1)
        check('collecting twice is refused', d['Error'] != 0)

        print('\nrelic levelling, selling and locking')
        for _ in range(3):
            p.add_scenecard(bonus[0][0])
        target = p.scenecards()[-1]
        mats = [x['uid'] for x in p.scenecards()[-3:-1]]
        owned = len(p.scenecards())
        d = call(s, 30106, _uid=target['uid'], vecMaterial=mats)
        check('feeding relics works', d['Error'] == 0, d['Error'])
        check('the target gained EXP', target['exp'] > 0, target['exp'])
        check('the material is gone', len(p.scenecards()) == owned - 2)

        keep = p.scenecards()[0]
        call(s, 30183, uid=keep['uid'], LockEnable=1)
        check('a relic can be locked', keep['lock'] == 1)
        owned = len(p.scenecards())
        call(s, 30106, _uid=target['uid'], vecMaterial=[keep['uid']])
        check('a locked relic is not eaten', len(p.scenecards()) == owned)
        call(s, 30161, _vecSell=[keep['uid']])
        check('and it is not sold either', len(p.scenecards()) == owned)
        call(s, 30183, uid=keep['uid'], LockEnable=0)
        gold = p.get_resource(0)
        d = call(s, 30161, _vecSell=[keep['uid']])
        check('an unlocked relic sells', d['Error'] == 0 and
              len(p.scenecards()) == owned - 1)
        check('and pays gold', p.get_resource(0) >= gold, p.get_resource(0))

        print('\nawakening nodes can be switched')
        target_unit = p.d['units'][2]
        node = sorted(arena.awaken_nodes(target_unit['id']))[0]
        target_unit['awaken'] = [node]
        d = call(s, 30093, uid=target_unit['uid'], partsInfoID=node)
        check('a recallable node switches off', d['Error'] == 0, d['Error'])
        check('and leaves the open list', node not in target_unit['awaken'])
        d = call(s, 30093, uid=target_unit['uid'], partsInfoID=node)
        check('and back on again', d['Error'] == 0
              and node in target_unit['awaken'])
        d = call(s, 30093, uid=target_unit['uid'], partsInfoID=999999)
        check('a node the hero lacks is refused', d['Error'] != 0)

    finally:
        os.environ.pop('HC_SETTINGS', None)
        shutil.rmtree(sandbox, ignore_errors=True)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        print('FAILED: ' + ', '.join(FAIL))
        return 1
    print('ALL CHECKS PASSED (%d)' % len(PASS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
