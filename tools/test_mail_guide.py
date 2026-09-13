"""Mail and the Guide Mission, driven through the real handlers.

    python tools/test_mail_guide.py

Mail's whole point is delivery to an account that is *online*, so the key check
here is that a post sent from another thread -- as the dashboard does -- rides
out on the session's very next Ack, with no reconnect.  The dashboard's HTTP
side is exercised against a real server on a spare port.

Everything runs in a sandbox against encoded packets.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
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

    def __init__(self, player=None):
        self.player = player
        self.account = player.account_id if player else None
        self.sent = []

    async def send(self, packet_id, *args):
        from hc.protocol.dto import encode_packet, decode_packet
        body = encode_packet(packet_id, *args)
        decoded = decode_packet(packet_id, body)
        assert not decoded.get('_trailing'), (
            'packet %d left %d trailing bytes' % (packet_id, decoded['_trailing']))
        self.sent.append((packet_id, decoded))
        return decoded

    def last(self, packet_id=None):
        for pid, d in reversed(self.sent):
            if packet_id is None or pid == packet_id:
                return d
        return None


def call(session, packet_id, **args):
    from hc.net import HANDLERS
    args.setdefault('_name', 'test')
    asyncio.run(HANDLERS[packet_id](session, args))
    return session.last()


def login(account_id, device):
    s = FakeSession()
    call(s, 30000, DeviceID=device, AccountID=account_id, AuthType=20,
         MarketID=0, ClientVersion='test', statDeviceID=device, DeviceType=0,
         UserLanguage=0, CheckKT=0, ResourceVersion=0, IsReconnect=False)
    return s


def http(base, path, method='GET', body=None):
    req = urllib.request.Request(
        base + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={} if body is None else {'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def main():
    sandbox = tempfile.mkdtemp(prefix='hc-mailguide-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    accounts = os.path.join(sandbox, 'accounts')
    os.makedirs(accounts)

    from hc.game import player as player_mod
    player_mod.ACCOUNTS_DIR = accounts
    from hc.webui import server as web
    web.ACCOUNTS_DIR = accounts
    web.INDEX = os.path.join(accounts, 'index.json')
    import hc.handlers.center as center
    center.ACCOUNTS_DIR = accounts
    center.INDEX = os.path.join(accounts, 'index.json')

    from hc.game.player import Player
    from hc.game import mail, missions, guide
    from hc.game.enums import CollectionType, ResourceType
    from hc.protocol.dto import TYPES
    from hc.data.tables import TABLES
    import hc.handlers                                    # registers handlers

    httpd = None
    try:
        # ==================================================================
        print('\nmail to an account that is online')
        p = Player.create(900400, 'test-device-mail')
        s = FakeSession(p)
        gold = p.get_resource(ResourceType.Gold)

        # The dashboard runs in its own thread; send from one.
        t = threading.Thread(target=lambda: mail.send(
            p.account_id, 'A gift', 'From the dashboard',
            [(ResourceType.Gold, -1, -1, 12345), (16, 119, -1, 50)]))
        t.start()
        t.join()
        check('the post waits in the outbox', mail.pending(p.account_id) == 1)
        check('and survives a restart on disk',
              os.path.exists(mail._outbox_path(p.account_id)))

        d = call(s, 30022)                    # any Ack carrying NGCheckServerInfo
        arrived = d['_CheckInfo'].vecAddPost or []
        check('it rides out on the very next Ack', len(arrived) == 1, arrived)
        check('with its subject and attachments', arrived and
              arrived[0].Subjects == 'A gift' and len(arrived[0].vecRewardInfo) == 2)
        check('the outbox is empty afterwards', mail.pending(p.account_id) == 0
              and not os.path.exists(mail._outbox_path(p.account_id)))
        check('and the post is in the saved mailbox',
              len(Player.load(p.account_id).posts()) == 1)
        d = call(s, 30022)
        check('it is delivered only once', not (d['_CheckInfo'].vecAddPost or []))

        uid = arrived[0].UID
        d = call(s, 30019, _info=TYPES['NGPostInfo'](UID=uid))
        check('claiming it works', d['Error'] == 0, d['Error'])
        check('the attachments are paid',
              p.get_resource(ResourceType.Gold) == gold + 12345
              and p.get_resource(16, 119) >= 50)
        changed = d['_CheckInfo'].vecChangePost or []
        check('the post stays, marked received',
              changed and changed[0].UID == uid and changed[0].ReceivedReward)
        d = call(s, 30019, _info=TYPES['NGPostInfo'](UID=uid))
        check('claiming it twice is refused', d['Error'] != 0)
        check('and pays nothing more',
              p.get_resource(ResourceType.Gold) == gold + 12345)

        print('\nclaim all, by type')
        mail.send(p.account_id, 'sys', '', [(ResourceType.Gold, -1, -1, 1)])
        mail.send(p.account_id, 'event', '', [(ResourceType.Gold, -1, -1, 1)],
                  post_type=mail.EVENT)
        call(s, 30022)
        d = call(s, 30020, _Type=mail.EVENT)
        check('claiming one type leaves the others',
              len(d['_CheckInfo'].vecChangePost or []) == 1)
        d = call(s, 30020, _Type=mail.ALL)
        check('-1 claims everything left',
              len(d['_CheckInfo'].vecChangePost or []) == 1)

        print('\ndeleting')
        before = len(p.posts())
        d = call(s, 30021, vecDelPost=[TYPES['NGPostInfo'](UID=uid)])
        check('a post can be deleted', len(p.posts()) == before - 1
              and len(d['_CheckInfo'].vecDelPost or []) == 1)

        print('\nplayer to player')
        other = Player.create(900401, 'test-device-mail-2')
        d = call(s, 30018, AccountIDTarget=other.account_id, postType=mail.USER,
                 Subjects='hi', Contents='hello')
        check('a post to another account is accepted', d['Error'] == 0)
        check('and waits for them', mail.pending(other.account_id) == 1)
        d = call(s, 30018, AccountIDTarget=999999, postType=mail.USER,
                 Subjects='hi', Contents='hello')
        check('a post to no one is refused', d['Error'] != 0)

        print('\nmail to an account that is offline')
        mail.send(other.account_id, 'while you were out', '',
                  [(ResourceType.Gold, -1, -1, 7)])
        mail._PENDING.clear()                 # simulate a server restart
        ls = login(other.account_id, 'test-device-mail-2')
        large = ls.last(40005)
        posts = large['ngAck'].vecPostInfo if large else []
        check('both posts are in the login payload', len(posts) == 2,
              [x.Subjects for x in posts])

        print('\nexpiry')
        old = mail.make('old', remove_hours=1,
                        now=datetime.utcnow() - timedelta(hours=2))
        old['uid'] = 1
        p.posts().append(old)
        check('an expired post is pruned', mail.prune(p) == 1)

        # ==================================================================
        print('\nthe dashboard')
        port = int(os.environ.get('HC_TEST_WEB_PORT', '18102'))
        httpd = web.start(bind='127.0.0.1', port=port, token='')
        base = 'http://127.0.0.1:%d/api' % port
        code, body = http(base, '/accounts/%d/mail' % p.account_id, 'POST', {
            'subject': 'From the web', 'contents': 'enjoy',
            'rewards': [{'key': '0:-1:-1', 'amount': 500}]})
        check('the dashboard can mail one account', code == 200 and body['sent'] == 1,
              body)
        check('it reports the post as on its way', body.get('pending') == 1, body)
        code, body = http(base, '/accounts/%d/mail' % p.account_id, 'POST',
                          {'subject': '', 'rewards': []})
        check('a post without a subject is refused', code >= 400, code)
        code, body = http(base, '/accounts/%d/mail' % p.account_id, 'POST', {
            'subject': 'bad', 'rewards': [{'key': 'nonsense', 'amount': 1}]})
        check('a malformed attachment is refused', code >= 400, code)
        code, body = http(base, '/mail', 'POST', {
            'accounts': 'all', 'subject': 'Everyone', 'rewards': []})
        check('the dashboard can mail every account', code == 200
              and body['sent'] >= 2, body)
        d = call(s, 30022)
        subjects = [x.Subjects for x in (d['_CheckInfo'].vecAddPost or [])]
        check('the online account gets both on its next action',
              'From the web' in subjects and 'Everyone' in subjects, subjects)
        code, body = http(base, '/accounts/%d/mail' % p.account_id)
        check('the dashboard can read the mailbox', code == 200
              and any(m['subject'] == 'From the web' for m in body['mailbox']), body)

        # ==================================================================
        print('\nthe Guide Mission at login')
        g = Player.create(900402, 'test-device-guide')
        gs = login(g.account_id, 'test-device-guide')
        ack02 = gs.last(40001)
        chapters = ack02['ngAck'].vecUserGuideMissionChapter
        check('four chapters go out', len(chapters) == 4, len(chapters))
        check('none has its reward claimed', all(c.RewardGained == 0 for c in chapters))
        large = gs.last(40005)
        guide_ids = {m.ID for m in large['ngAck'].vecMissionInfo
                     if m.Type == missions.GUIDE}
        check('all 147 guide missions go out', len(guide_ids) == 147, len(guide_ids))
        check('each with a lifetime baseline of 0', all(
            m.StartCollectionValue == 0 for m in large['ngAck'].vecMissionInfo
            if m.Type == missions.GUIDE))

        multi = ack02['ngAck'].vecMissionMultiConditionInfo or []
        check('the multi-condition guide missions get their record',
              sorted(m.ID for m in multi) == [100006, 100021, 100022, 100028],
              [m.ID for m in multi])

        g = Player.load(g.account_id)
        gsess = FakeSession(g)

        print('\na stage-clear mission counts a stage cleared earlier')
        row = missions.row(100002)            # "Clear the stage 1-3 of Season 0"
        g.d['missions'].pop('100002', None)
        g.set_collection(CollectionType.DungeonClearCount, 7, t2=3)
        check('its baseline is still 0', missions.state(g, row)['start'] == 0)
        reward_before = {(t1, t2): g.get_resource(t1, t2)
                         for t1, t2, _t3, _v in missions.reward(row)}
        d = call(gsess, 30029, vecMission=[100002], bShowEnable=True)
        check('claiming it works', d['Error'] == 0, d['Error'])
        check('and it pays', any(g.get_resource(t1, t2) > v
                                 for (t1, t2), v in reward_before.items())
              or not reward_before)

        print('\nthe counters the Guide Mission reads')
        before = g.get_collection(CollectionType.AttendanceCount)
        missions.record_login(g)
        check('a login already counted today does not count twice',
              g.get_collection(CollectionType.AttendanceCount) == before)
        missions.record_login(g, now=datetime.utcnow() + timedelta(days=1))
        check('the next day it does',
              g.get_collection(CollectionType.AttendanceCount) == before + 1)

        hero = next(u for u in g.d['units'] if int(u['id']) == 77)
        call(gsess, 30001, uid=hero['uid'])
        check('levelling a hero records its level',
              g.get_collection(CollectionType.UnitLevelUp, t2=77) == hero['level'],
              g.get_collection(CollectionType.UnitLevelUp, t2=77))
        call(gsess, 30003, uid=hero['uid'], vecMaterialUnitID=[])
        check('starring a hero up records the grade it reached',
              g.get_collection(CollectionType.GetUnitCount, t2=77,
                               t3=int(hero['grade'])) == 1)
        node = sorted(__import__('hc.game.arena', fromlist=['x']).awaken_nodes(77))[0]
        before = g.get_collection(CollectionType.AwakenPartsSlotOpenCount, t2=node)
        call(gsess, 30004, uid=hero['uid'], vecPartsInfoID=[node])
        check('opening an awakening node records it',
              g.get_collection(CollectionType.AwakenPartsSlotOpenCount, t2=node)
              == before + 1)
        row = missions.row(100109)            # "Level Up Hero" x30, lifetime
        done, need = missions.progress(g, row)
        level = int(hero['level'])
        call(gsess, 30001, uid=hero['uid'])
        check('a hero level-up counts towards "Level Up Hero"',
              int(hero['level']) == level + 1
              and missions.progress(g, row) == (done + 1, need),
              (missions.progress(g, row), done, need))

        from hc.game import equipment as equipment_mod
        from hc.data.tables import to_int
        # The in-place upgrade of worn gear (30066) always succeeds, so it
        # gives a deterministic check.
        row5 = next(r for r in TABLES.sql('itemList')
                    if to_int(r.get('itemType'), -1) == 1
                    and equipment_mod.grade_up_recipe(to_int(r['itemID'])))
        item = to_int(row5['itemID'])
        recipe = equipment_mod.grade_up_recipe(item)
        hero.setdefault('equip', {})['1'] = item
        g.add_resource(equipment_mod.ITEM, recipe['count'], recipe['material'])
        g.add_resource(ResourceType.Gold, recipe['gold'] + 1)
        before = g.get_collection(CollectionType.ItemGradeUpCount)
        d = call(gsess, 30066, uidUnit=hero['uid'], itemType=1)
        check('an equipment upgrade counts towards "Upgrade Equipment"',
              d['Error'] == 0 and hero['equip']['1'] == recipe['result']
              and g.get_collection(CollectionType.ItemGradeUpCount) == before + 1,
              (d['Error'], g.get_collection(CollectionType.ItemGradeUpCount)))
        progress = missions.progress(g, missions.row(100142))   # "Upgrade Equipment" x5
        check('and the mission reads that counter',
              progress == (g.get_collection(CollectionType.ItemGradeUpCount), 5), progress)

        before = g.get_collection(CollectionType.ArtifactGradeGetCount, t2=5)
        artifact_id = next(a for a in __import__('hc.game.artifacts', fromlist=['x'])
                           .catalogue(grades=(5,)))
        g.add_artifact(artifact_id)
        check('getting an artifact records its grade',
              g.get_collection(CollectionType.ArtifactGradeGetCount, t2=5) == before + 1)

        before = g.get_collection(CollectionType.SceneCardGradeGetCount, t2=5)
        g.add_scenecard(95007)
        check('getting a relic records its grade',
              g.get_collection(CollectionType.SceneCardGradeGetCount, t2=5)
              == before + 1)

        print('\nsummons are counted without changing the summon Ack')
        before = g.get_collection(CollectionType.GachaOpenCount, t2=1)
        missions.record_summon(g, 1, 3)
        g.take_dirty_collections()
        missions.record_summon(g, 1, 1)
        from hc.handlers import gacha as gh
        g.set_gacha_result(1, g.d['units'][0]['id'], 3)
        sync, _count = gh.summon_ack_shape(g, 1, [])
        check('the safe summon Ack still carries no collections',
              not (sync.vecAddCollectionInfo or []))
        d = call(gsess, 30022)
        keys = [(c.Type1, c.Type2) for c in (d['_CheckInfo'].vecAddCollectionInfo or [])]
        check('but the summon counter goes out on the next Ack',
              (CollectionType.GachaOpenCount, 1) in keys, keys)
        check('and it counted every hero summoned',
              g.get_collection(CollectionType.GachaOpenCount, t2=1) == before + 4)

        print('\nbackfill for progress made before these counters existed')
        fresh = Player.create(900403, 'test-device-guide-2')
        fresh.d['units'][0]['level'] = 42
        fresh.d['collections'] = {}
        missions.backfill(fresh)
        check('hero levels are rebuilt from the save',
              fresh.get_collection(CollectionType.UnitLevelUp,
                                   t2=int(fresh.d['units'][0]['id'])) == 42)
        check('and so is the level-up count, one per level above 1',
              fresh.get_collection(CollectionType.AllUnitLevelUpCount,
                                   t2=int(fresh.d['units'][0]['id'])) >= 41)
        check('owned artifacts are counted by grade',
              sum(v for t1, _a, _b, v in fresh.collection_items()
                  if t1 == CollectionType.ArtifactGradeGetCount)
              == len(fresh.d.get('artifacts', [])))
        missions.backfill(fresh)
        check('and running it again changes nothing',
              fresh.get_collection(CollectionType.UnitLevelUp,
                                   t2=int(fresh.d['units'][0]['id'])) == 42)

        print("\na chapter's final reward")
        d = call(gsess, 30307, _chapterID=1)
        check('refused while missions are unclaimed', d['_iError'] != 0)
        for mid in guide.chapter_mission_ids(1):
            missions.state(g, missions.row(mid))['received'] = True
        before = {(t1, t2): g.get_resource(t1, t2)
                  for t1, t2, _t3, _v in guide.final_reward(guide.chapter(1))}
        d = call(gsess, 30307, _chapterID=1)
        check('paid once every mission is claimed', d['_iError'] == 0, d['_iError'])
        check('the reward arrives', all(
            g.get_resource(t1, t2) > before[(t1, t2)]
            for t1, t2, _t3, _v in guide.final_reward(guide.chapter(1))))
        check('RewardGained is now 1', d['_UserChapterInfo'].RewardGained == 1)
        d = call(gsess, 30307, _chapterID=1)
        check('and it cannot be claimed twice', d['_iError'] != 0)
        d = call(gsess, 30307, _chapterID=99)
        check('a chapter that does not exist is refused', d['_iError'] != 0)
    finally:
        if httpd is not None:
            httpd.shutdown()
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
