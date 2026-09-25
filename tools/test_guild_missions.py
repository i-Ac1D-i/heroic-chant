"""Guilds, exclusive equipment and missions, driven through the real handlers.

    python tools/test_guild_missions.py

Covers the fixes for five things reported from real play:

* the 1v1 Arena daily reward did nothing when claimed
* the Guild War defence team could not be saved, and the screen could not be
  left without restarting the game
* guild buffs stopped upgrading
* the daily guild donation never reset
* exclusive equipment could not be upgraded

Everything runs in a sandbox against encoded packets, so a DTO that does not
marshal fails here rather than on the phone.
"""
import asyncio
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

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
        self.sent.append((packet_id, decoded))
        return decoded

    def last(self):
        return self.sent[-1][1] if self.sent else None


def call(session, packet_id, **args):
    from hc.net import HANDLERS
    args.setdefault('_name', 'test')
    asyncio.run(HANDLERS[packet_id](session, args))
    return session.last()


def main():
    sandbox = tempfile.mkdtemp(prefix='hc-guildmission-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    accounts = os.path.join(sandbox, 'accounts')
    os.makedirs(accounts)

    from hc.game import player as player_mod
    player_mod.ACCOUNTS_DIR = accounts
    from hc.game.player import Player
    from hc.game import guild, equipment, missions
    from hc.game.enums import CollectionType, ResourceType
    from hc.protocol.dto import TYPES
    import hc.handlers                                    # registers handlers

    try:
        p = Player.create(900300, 'test-device-guild-missions')
        s = FakeSession(p)

        # ------------------------------------------------------------------
        print('\nthe 1v1 Arena daily mission')
        row = missions.row(104)
        check('mission 104 is the daily 1v1 arena one',
              row is not None and row['missionType'] == '1'
              and row['ClearVal_2'] == str(missions.ARENA_CONTENTS), row)
        # MissionClearType 12 reads CollectionType 13 -- different enums.
        check('clear type 12 reads the ContentsPlayCount counter (13)',
              missions.CLEAR_TO_COLLECTION[12] == CollectionType.ContentsPlayCount == 13)
        check('the daily missions go out at login',
              any(i.ID == 104 for i in missions.infos(p)))

        d = call(s, 30029, vecMission=[104], bShowEnable=True)
        check('claiming before playing is refused', d['Error'] != 0, d['Error'])

        need = int(row['ClearVal_1'])
        gold = p.get_resource(0)
        for n in range(need):
            missions.record_arena_fight(p, won=(n % 2 == 0))
        done, _ = missions.progress(p, row)
        check('arena fights advance the mission', done == need, done)
        check('wins also count separately',
              p.get_collection(CollectionType.ContentsClearCount,
                               t2=missions.ARENA_CONTENTS) > 0)

        rewards_before = {(t1, t2): p.get_resource(t1, t2)
                          for t1, t2, _t3, _v in missions.reward(row)}
        d = call(s, 30029, vecMission=[104], bShowEnable=True)
        check('claiming it now works', d['Error'] == 0, d['Error'])
        paid = all(p.get_resource(t1, t2) > rewards_before[(t1, t2)]
                   for t1, t2, _t3, _v in missions.reward(row))
        check('and the reward is actually paid', paid, missions.reward(row))
        changed = d['_CheckInfo'].vecChangeMissionInfo or []
        check('the client is told it was received',
              any(m.ID == 104 and m.IsReceived for m in changed), changed)

        d = call(s, 30029, vecMission=[104], bShowEnable=True)
        check('claiming it twice is refused', d['Error'] != 0, d['Error'])

        tomorrow = datetime.utcnow() + timedelta(days=1)
        entry = missions.state(p, row, now=tomorrow)
        check('the next day it resets', not entry['received']
              and missions.progress(p, row, now=tomorrow)[0] == 0)

        # The baseline is pinned before a fight, so a restart mid-day followed
        # by a fight still counts that fight.
        p.d['missions'].pop('104', None)
        before = p.get_collection(CollectionType.ContentsPlayCount,
                                  t2=missions.ARENA_CONTENTS)
        missions.record_arena_fight(p, won=False)
        check('a fight right after a fresh baseline still counts',
              missions.progress(p, row)[0] == 1,
              missions.progress(p, row))

        # ------------------------------------------------------------------
        print('\nthe 1v1 Arena daily points ladder (missions 1001-1004)')
        ladder = [missions.row(m) for m in (1001, 1002, 1003, 1004)]
        # The fights above already earned points today; start from a fresh
        # baseline so the numbers below are this section's alone.
        for m in (1001, 1002, 1003, 1004):
            p.d['missions'].pop(str(m), None)
        check('they are ArenaDaily missions scored on ArenaAttackWinPoint',
              all(r['missionType'] == '9' and r['ClearType'] == '23' for r in ladder))
        infos = {i.ID: i for i in missions.infos(p)}
        check('all four go out at login', all(m in infos for m in (1001, 1002, 1003, 1004)))
        check('clear type 23 reads collection 22',
              missions.CLEAR_TO_COLLECTION[23] == CollectionType.ArenaAttackWinPoint == 22)
        pts = lambda: missions.progress(p, ladder[0])[0]
        base = pts()
        missions.record_arena_fight(p, won=True)
        check('a win is worth 3 points', pts() == base + 3, pts())
        missions.record_arena_fight(p, won=False)
        check('a loss is worth 1', pts() == base + 4, pts())
        check('the start value the client subtracts is today\'s baseline',
              infos[1001].StartCollectionValue
              == missions.state(p, ladder[0])['start'])

        d = call(s, 30029, vecMission=[1001, 1002], bShowEnable=True)
        changed = {m.ID for m in (d['_CheckInfo'].vecChangeMissionInfo or []) if m.IsReceived}
        check('2 and 4 points can be claimed', changed == {1001, 1002}, changed)
        d = call(s, 30029, vecMission=[1003], bShowEnable=True)
        check('8 points cannot yet', d['Error'] != 0, d['Error'])

        # Through the real arena result handler: the counter reaches the client.
        from hc.game import arena
        arena.record(p)['matches'] = []
        before = p.get_collection(CollectionType.ArenaAttackWinPoint)
        d = call(s, 30058, iWinLose=1, iMyTotalPower=1, iMatchTotalPower=1,
                 _iMatchUserAccount=-1, _iMatchIndex=0, dungeonID=0,
                 strRecordInfo='', AuthEnable=False, _LogString='')
        check('an arena win through 30058 adds the points',
              p.get_collection(CollectionType.ArenaAttackWinPoint) == before + 3)
        sent = [(c.Type1, c.Value1) for c in (d['_CheckInfo'].vecAddCollectionInfo or [])]
        check('and the new total reaches the client',
              (22, before + 3) in [(t, v) for t, v in sent], sent)

        tomorrow = datetime.utcnow() + timedelta(days=1)
        check('the ladder starts again the next day',
              missions.progress(p, ladder[0], now=tomorrow)[0] == 0
              and not missions.state(p, ladder[1], now=tomorrow)['received'])

        # ------------------------------------------------------------------
        print('\na guild to test with')
        p.add_resource(ResourceType.Gold, 50_000_000)
        d = call(s, 30082, _Guild=TYPES['NGGuild'](
            Name='TestGuild', Introduction='', MarkFlag=0, MarkColorIndex=0,
            JoinRank=1, JoinType=0))
        check('a guild can be founded', p.d.get('guild') is not None, d)

        # ------------------------------------------------------------------
        print('\nthe guild missions (MissionType 5 daily, 6 weekly)')
        infos = {i.ID: i for i in missions.infos(p)}
        check('the guild missions go out with the others',
              all(m in infos for m in (20001, 20002, 20003, 20004, 30001, 30002)),
              sorted(m for m in infos if m >= 20000))
        donate = missions.row(20002)
        check('20002 is "donate once", scored on collection 20',
              donate['ClearType'] == '21'
              and missions.CLEAR_TO_COLLECTION[21] == CollectionType.GuildDonationCount == 20)
        d = call(s, 30029, vecMission=[20002], bShowEnable=True)
        check('it cannot be claimed before donating', d['Error'] != 0)

        print('\nthe daily guild donation')
        cap = guild.donation_cap()
        call(s, 30095)
        check('a donation counts for the mission', missions.progress(p, donate) == (1, 1),
              missions.progress(p, donate))
        d = call(s, 30029, vecMission=[20002], bShowEnable=True)
        check('and then it can be claimed', d['Error'] == 0, d['Error'])
        tomorrow = datetime.utcnow() + timedelta(days=1)
        check('the next day it starts again',
              missions.progress(p, donate, now=tomorrow)[0] == 0
              and not missions.state(p, donate, now=tomorrow)['received'])
        week = missions.row(30001)
        check('the weekly one resets by the week, not the day',
              missions.period(6, datetime(2026, 9, 21)) == missions.period(6, datetime(2026, 9, 27))
              and missions.period(6, datetime(2026, 9, 21)) != missions.period(6, datetime(2026, 9, 28))
              and week['missionType'] == '6')
        for _ in range(cap - 1):
            call(s, 30095)
        check('the day fills up', p.d['guild']['donation_count'] == cap,
              p.d['guild']['donation_count'])
        d = call(s, 30095)
        check('one more is refused', d['Error'] != 0)
        # Simulate yesterday's cap still sitting in the save, as a real account
        # would have it the morning after.
        p.d['guild']['donation_day'] = (datetime.utcnow() - timedelta(days=1)) \
            .date().isoformat()
        member = guild.guild_member_dto(p)
        check('a new day reports a zero count to the client',
              member.DonationCount == 0, member.DonationCount)
        d = call(s, 30095)
        check('and donating works again', d['Error'] == 0, d['Error'])
        check('tmLastDonation is the real last donation, not "now" forever',
              guild.guild_member_dto(p).tmLastDonation.year > 2000)

        # ------------------------------------------------------------------
        print('\nguild buffs')
        passive = 1
        d = call(s, 30210, _PassiveID=passive)
        check('a buff can be levelled up', d['Error'] == 0, d['Error'])
        check('and it is on the guild', any(
            b.PassiveID == passive for b in guild.guild_dto(p).vecBuff))
        # Let its timer run out.
        p.d['guild']['buffs'][str(passive)]['until'] = \
            (datetime.utcnow() - timedelta(hours=1)).isoformat(timespec='seconds')
        expired = [b for b in guild.guild_dto(p).vecBuff if b.PassiveID == passive]
        check('an expired buff is still sent, so its level is not lost',
              len(expired) == 1, expired)
        check('with the level it really has', expired and
              guild.buff(expired[0].BuffUID) is not None
              and guild.buff(expired[0].BuffUID)['BuffLevel'] == '1')
        d = call(s, 30210, _PassiveID=passive)
        check('and it can be upgraded again after expiring', d['Error'] == 0,
              d['Error'])
        check('to level 2', p.d['guild']['buffs'][str(passive)]['level'] == 2)

        # ------------------------------------------------------------------
        print('\nthe Guild War defence team')
        units = p.d['units'][:3]
        rows = [TYPES['NGPartyInfo'](SlotType=0, SlotIndex=i,
                                     UnitUID=int(u['uid']), SkillOnOff=1)
                for i, u in enumerate(units)]
        d = call(s, 30250, _vecChangePartyInfo=rows)
        check('saving the team gets an Ack, so the screen can close',
              s.sent[-1][0] == 40273 and d['Error'] == 0, d)
        check('the Ack echoes the team', len(d['_vecChangePartyInfo']) == 3)
        member = guild.guild_member_dto(p)
        check('the team is kept on the guild member',
              len(member.vecGuildWarParty) == 3, member.vecGuildWarParty)
        check('with its heroes', len(member.vecGuildWarUnit) == 3)
        bogus = [TYPES['NGPartyInfo'](SlotType=0, SlotIndex=0, UnitUID=999999,
                                      SkillOnOff=1)]
        call(s, 30250, _vecChangePartyInfo=bogus)
        check('a hero the player does not own is dropped',
              len(guild.guild_member_dto(p).vecGuildWarParty) == 0)

        # ------------------------------------------------------------------
        print('\nexclusive equipment')
        base = 5001
        recipe = equipment.exclusive_recipe(base)
        check('5001 is an exclusive item with a next grade',
              equipment.is_exclusive(base) and recipe is not None)

        # Fusion: FusionCount copies, no roll, no gold.
        p.add_resource(equipment.ITEM, recipe['fusion'], base)
        gold = p.get_resource(ResourceType.Gold)
        d = call(s, 30216, GradeUpType=equipment.GRADEUP_FIX, itemID=base)
        check('fusing enough copies works', d['Error'] == 0 and d['_GradeUpSuccess'],
              d)
        check('it consumes the copies', p.get_resource(equipment.ITEM, base) == 0)
        check('and yields the next grade',
              p.get_resource(equipment.ITEM, recipe['result']) == 1)
        check('without charging gold', p.get_resource(ResourceType.Gold) == gold)
        d = call(s, 30216, GradeUpType=equipment.GRADEUP_FIX, itemID=base)
        check('fusing with too few copies is refused', d['Error'] != 0)

        # A worn copy counts towards FusionCount.
        hero = p.d['units'][0]
        hero.setdefault('equip', {})[str(equipment.EXCLUSIVE_SLOT)] = base
        p.add_resource(equipment.ITEM, recipe['fusion'] - 1, base)
        d = call(s, 30217, baseUnitUID=hero['uid'],
                 GradeUpType=equipment.GRADEUP_FIX)
        check('a worn copy counts towards fusion', d['Error'] == 0, d['Error'])
        check('and the hero now wears the result',
              int(hero['equip'][str(equipment.EXCLUSIVE_SLOT)]) == recipe['result'])
        check('the changed hero goes back to the client',
              len(d['_CheckInfo'].vecChangeUnitInfo or []) == 1)

        # Enhance: materials and gold, then a roll.
        hero['equip'][str(equipment.EXCLUSIVE_SLOT)] = base
        for t1, t2, amount in equipment.enhance_costs(recipe):
            if t1 == ResourceType.Item:
                p.add_resource(t1, amount * 50, t2)
        attempts = successes = 0
        while attempts < 40:
            hero['equip'][str(equipment.EXCLUSIVE_SLOT)] = base
            d = call(s, 30217, baseUnitUID=hero['uid'],
                     GradeUpType=equipment.GRADEUP_NORMAL)
            if d['Error'] != 0:
                break
            attempts += 1
            successes += 1 if d['_GradeUpSuccess'] else 0
        check('enhancing works', attempts > 0, d['Error'])
        check('and it rolls -- some succeed and some fail over 40 tries',
              0 < successes < attempts, '%d of %d' % (successes, attempts))

        mat = recipe['material']
        p.add_resource(equipment.ITEM, -p.get_resource(equipment.ITEM, mat), mat)
        hero['equip'][str(equipment.EXCLUSIVE_SLOT)] = base
        d = call(s, 30217, baseUnitUID=hero['uid'],
                 GradeUpType=equipment.GRADEUP_NORMAL)
        check('enhancing without materials is refused', d['Error'] != 0)
        check('and the item is untouched',
              int(hero['equip'][str(equipment.EXCLUSIVE_SLOT)]) == base)
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
