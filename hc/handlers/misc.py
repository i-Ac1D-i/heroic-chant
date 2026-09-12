"""Odds and ends: cheats, attendance, missions, events."""
import logging

from ..net import handler
from ..protocol.dto import TYPES
from ..game import state
from ..game.errors import Err
from ..game.enums import NMError, ResourceType

log = logging.getLogger('hc.misc')


@handler(30110)
async def change_nickname(s, a):
    """Rename. `MyInfoChangeNickname` has both a paid button (`costValue`, a
    ResourceReward) and a free one (`btnFreeChangeName`), but both send this
    same packet with nothing to tell them apart, and no table anywhere ships a
    price for a rename -- so charging would be inventing an economy rule, not
    reading one. Nothing is charged; only the length is checked, with the
    client's own `Error_MyInfoChangeNickNameLength` (1116) so the popup reads
    right instead of the request just silently failing.
    """
    p = s.player
    name = (a['_ChangeName'] or '').strip()
    if not name:
        await s.send(40122, NMError.Error_MyInfoChangeNickNameLength,
                     state.user_info(p), state.resource_sync(p))
        return
    p.d['nickname'] = name
    p.save()
    log.info('account %d renamed to %r', p.account_id, name)
    await s.send(40122, Err.OK, state.user_info(p), state.resource_sync(p))


@handler(30111)
async def change_represent_hero(s, a):
    """Change Main Hero. `RepresentProfileID` is a `UnitID` (the hero
    definition, from the `profileInfo`/`representProfile` tables), not an
    owned unit's instance uid -- `NMUnit.GetProfileList` builds the picker's
    candidates from `profileInfo` and only keeps the ones where
    GetResourceLongValue(ResourceType.Profile, UnitID, -1) >= 1, so that is
    the same thing to check here (see `Player.add_unit`, which now grants
    that resource). Refuse one the account hasn't unlocked
    (`Error_MyInfoChangeProfileNotSelect`) or one already in use
    (`Error_MyInfoChangeProfileSameProfile`); either way the client has a
    named popup for it, so there is no reason to invent a generic failure.
    """
    p = s.player
    unit_id = int(a['RepresentProfileID'])
    if unit_id == p.d['represent_profile']:
        await s.send(40123, NMError.Error_MyInfoChangeProfileSameProfile, state.user_info(p))
        return
    if p.get_resource(ResourceType.Profile, unit_id, -1) < 1:
        await s.send(40123, NMError.Error_MyInfoChangeProfileNotSelect, state.user_info(p))
        return
    p.d['represent_profile'] = unit_id
    p.save()
    log.info('account %d set represented hero to unit %d', p.account_id, unit_id)
    await s.send(40123, Err.OK, state.user_info(p))


@handler(30283)
async def change_wallpaper(s, a):
    """Change the Background. Unlike the hero picker, `MyInfoChangeBackImage`
    builds its candidates from `GetResourceInfoListByType1(ResourceType.
    WallPaper=158)` -- ownership is a plain resource, not a per-hero reward
    table, and it only ever gets granted by a purchase (the Costume Shop's
    `BuyShopGoodsReq` pays some goods out in WallPaper -- see
    `hc/handlers/shop.py`), which is also why this screen carries its own
    `OnBuy()`. So an empty list before buying anything is correct, not a bug.
    There is no client error string dedicated to this screen, so an unowned
    id gets the generic `Err.NOT_FOUND`.
    """
    p = s.player
    wallpaper_id = int(a['wallPaperID'])
    if p.get_resource(ResourceType.WallPaper, wallpaper_id, -1) < 1:
        await s.send(40305, Err.NOT_FOUND, state.user_info(p))
        return
    p.d['wallpaper_id'] = wallpaper_id
    p.save()
    log.info('account %d changed background to wallpaper %d', p.account_id, wallpaper_id)
    await s.send(40305, Err.OK, state.user_info(p))


@handler(30305)
async def change_frame(s, a):
    """Change Frame. The picker (`NMUserInfo.GetFrameList`) reads its
    candidates from the account's own `vecUserAllFrames`, sent once at login
    (see `state.frame_infos`) -- nothing was ever sent there before, so the
    list was always empty and even the Release button (which just sends
    FrameID 0) bailed out client-side with `Error_MyInfoNotReleaseFrame`
    before a packet was ever written. FrameID 0 is that "no frame" sentinel
    and always allowed; the 26 real frames (ResourceTable's ResourceID 170)
    are earned (Arena rank, Tower clears, events), so anything else needs the
    matching `ResourceType.Frame` resource.
    """
    p = s.player
    frame_id = int(a['FrameID'])
    if frame_id == p.d.get('frame_id', 0):
        await s.send(40327, NMError.Error_MyInfoChangeFrameSameProfile, state.user_info(p))
        return
    if frame_id != 0 and p.get_resource(ResourceType.Frame, frame_id, -1) < 1:
        await s.send(40327, NMError.Error_MyInfoChangeFrameNotSelect, state.user_info(p))
        return
    p.d['frame_id'] = frame_id
    p.save()
    log.info('account %d changed frame to %d', p.account_id, frame_id)
    await s.send(40327, Err.OK, state.user_info(p))


@handler(30012)
async def cheat_get_resource(s, a):
    """The client's own debug path. Retail gated it server-side; offline it is
    the simplest way to hand yourself currency for testing."""
    p = s.player
    for r in a['vecInfo']:
        p.add_resource(r.Type1, r.Value1, r.Type2, r.Type3)
        log.info('cheat: +%d of resource %d/%d/%d', r.Value1, r.Type1, r.Type2, r.Type3)
    p.save()
    await s.send(40017, Err.OK, state.resource_sync(p), [])


@handler(30028)
async def attendance_reward(s, a):
    await s.send(40033, Err.OK, state.resource_sync(s.player))


@handler(30029)
async def get_mission_reward(s, a):
    p = s.player
    done = set(p.d.get('missions_done', []))
    done.update(a['vecMission'])
    p.d['missions_done'] = sorted(done)
    p.save()
    await s.send(40034, Err.OK, state.resource_sync(p), a['bShowEnable'])


@handler(30036)
async def check_event_info(s, a):
    await s.send(40041, Err.OK, TYPES['NGCheckEventInfo']())


@handler(30222)
async def change_chat_channel(s, a):
    await s.send(40245, Err.OK, a['_ChannelID'])


@handler(30310)
async def save_user_option(s, a):
    s.player.d['user_option'] = {f['name']: getattr(a['_option'], f['name'])
                                 for f in TYPES['NGUserOptionServer']._fields}
    s.player.save()
    await s.send(40332, Err.OK, a['_option'])


@handler(30145)
async def total_unit_power(s, a):
    """Client-computed power per unit; store it so the roster UI keeps its
    numbers across sessions.  No Ack exists for this one."""
    powers = {p.first: p.second for p in a['vecUnitPower']}   # (unitUID, power)
    for u in s.player.d['units']:
        if u['uid'] in powers:
            u['power'] = powers[u['uid']]
    s.player.save()


@handler(30138)
async def client_play_check_log(s, a):
    """Battle telemetry. Retail used it for cheat detection; we just note it."""
    log.debug('play log: contents=%s stage=%s type=%s',
              a['_ContentsType'], a['_Stage'], a['_LogType'])


@handler(30223)
async def tutorial_save(s, a):
    p = s.player
    tid = a['_TutorialID']
    if tid not in p.d['tutorials']:
        p.d['tutorials'].append(tid)
        p.save()
    await s.send(40246, tid, state.resource_sync(p))


@handler(30287)
async def latest_hottime(s, a):
    await s.send(40309, Err.OK, state.resource_sync(s.player))


@handler(30075)
async def alien_dungeon_total_info(s, a):
    await s.send(40081, [], [])


@handler(30357)
async def unit_favorites_change(s, a):
    """The heart on a hero card.  Cosmetic, but it rides NGUnitInfo.Favorite,
    so without it the flag is lost on the next login."""
    p = s.player
    unit = p.find_unit(a['_uid'])
    if unit is None:
        await s.send(40380, Err.NOT_FOUND, state.resource_sync(p))
        return
    unit['favorite'] = bool(a['_Favorites'])
    p.save()
    await s.send(40380, Err.OK,
                 state.resource_sync(p, vecChangeUnitInfo=state.unit_infos(p, [unit])))
