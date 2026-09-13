"""Core DTO builders: resources, collection counters, units, and the
``NGCheckServerInfo`` every Ack carries.

Feature-specific builders live next to their feature -- gacha DTOs in
``game/gacha.py``, shop DTOs in ``game/shop.py``, guild DTOs in
``game/guild.py``.  Those modules import this one; this one imports none of
them, which is what keeps the dependency graph acyclic.

Important: ``NGCheckServerInfo.vecAddResourceInfo`` carries **absolute**
values, not deltas, despite the name.  NMUserInfo.AddResource @0x138EC88
does ``dict[key] = incoming`` and only computes ``old.GetDifferenceValue(new)``
to drive the floating "+N" popup.  Sending deltas here would desync every
currency in the game.
"""
from datetime import datetime

from ..protocol.dto import TYPES
from .enums import ResourceType


def _dt(iso):
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return datetime(2000, 1, 1)


def resource_info(t1, t2, t3, value):
    return TYPES['NGResourceInfo'](
        Type1=t1, Type2=t2, Type3=t3, Value1=int(value),
        Value2=0, Value3=0, RegDate=datetime.utcnow())


def resource_infos(player, only=None):
    """Absolute resource snapshot; ``only`` limits it to specific (t1,t2,t3) keys."""
    out = []
    for t1, t2, t3, v in player.resource_items():
        if only is not None and (t1, t2, t3) not in only:
            continue
        out.append(resource_info(t1, t2, t3, v))
    return out


def collection_info(t1, t2, t3, value):
    return TYPES['NGCollectionInfo'](Type1=t1, Type2=t2, Type3=t3, Value1=int(value))


def collection_infos(player, only=None):
    """Absolute counter snapshot; NMUserInfo.AddCollection @0x137FC60 does
    dict[key] = incoming, so these are absolute just like resources."""
    return [collection_info(t1, t2, t3, v)
            for t1, t2, t3, v in player.collection_items()
            if only is None or (t1, t2, t3) in only]


def user_info(player):
    """NGUserInfo -- the in-game profile card: nickname, rank EXP, represented
    hero, frame, skin.  Distinct from `center.account_info`'s NGAccountInfo,
    which wraps the same handful of fields for the center-server login flow."""
    return TYPES['NGUserInfo'](
        NickName=player.nickname,
        Exp=player.d['exp'],
        RegDate=_dt(player.d['reg_date']),
        LastLoginDate=_dt(player.d.get('last_login', player.d['reg_date'])),
        RepresentProfile=player.d['represent_profile'],
        AccountID=player.account_id,
        SkinID=player.d['skin_id'],
        WallPaperID=player.d.get('wallpaper_id', 0),
        frameInfo=TYPES['NGFrameInfo'](frameID=player.d.get('frame_id', 0)),
    )


def frame_infos(player):
    """NGFrameInfo entries for NGLogInAck01.vecUserAllFrames -- the "Change
    Frame" picker's candidate list. NMUserInfo.GetFrameList reads this same
    list back from the login snapshot; without at least one entry it can
    never resolve the account's *current* selection either, which is why an
    empty list turned even the Release button into Error_MyInfoNotReleaseFrame
    (MyInfoChangeFrame.OnClickRelease bails out first if it can't find one).
    FrameID 0 is the wire's "no frame" sentinel -- OnClickRelease sends it
    unconditionally -- so it is always present here, unlike the 26 real
    frames (ResourceTable's ResourceID 170), which are earned (Arena rank,
    Tower clears, events) and only listed once owned. seasonType 0 marks a
    frame as not season-limited; GetFrameList only runs the
    GetNGSeasonValue/endSeason check for seasonType >= 1, so plain ownership
    is enough for everything granted here.
    """
    out = [TYPES['NGFrameInfo'](frameID=0, seasonType=0, endSeason=0)]
    for t1, t2, t3, v in player.resource_items():
        if t1 == ResourceType.Frame and v > 0:
            out.append(TYPES['NGFrameInfo'](frameID=t2, seasonType=0, endSeason=0))
    return out


def equip_info(u):
    """NGUnitEquipInfo per occupied slot: gear first, then rune slots.  Both
    ride the same list -- ItemType is the slot, ItemKey what is in it."""
    out = []
    for slot, key in sorted((u.get('equip') or {}).items(), key=lambda kv: int(kv[0])):
        out.append(TYPES['NGUnitEquipInfo'](
            UnitUID=u['uid'], ItemType=int(slot), ItemKey=int(key)))
    for slot, rune in sorted((u.get('runes') or {}).items(), key=lambda kv: int(kv[0])):
        rid = rune.get('rune') if isinstance(rune, dict) else rune
        out.append(TYPES['NGUnitEquipInfo'](
            UnitUID=u['uid'], ItemType=int(slot), ItemKey=int(rid or 0)))
    return out


def unit_info(u, relics=None):
    """One NGUnitInfo.

    `relics` is what this hero is wearing, as stored relic dicts.  They ride on
    the unit rather than being looked up client-side, which is what makes an
    *opponent's* relics renderable at all: the viewer does not own that relic
    and has no other way to learn what it is.  Falls back to ``u['relics']`` so
    a synthesised unit -- an arena bot -- can carry its own.
    """
    from . import artifacts, scenecards
    if relics is None:
        relics = u.get('relics') or ()
    cards = u.get('scenecards') or ()
    return TYPES['NGUnitInfo'](
        vecArtifactInfo=[artifacts.info(r) for r in relics],
        # Relics (Scene Cards) ride on the unit for the same reason artifacts
        # do: an opponent's client owns none of them and cannot look them up.
        vecSceneCardInfo=[scenecards.info(c) for c in cards],
        UID=u['uid'], ID=u['id'], Level=u.get('level', 1),
        Tier=u.get('tier', 1), Grade=u.get('grade', 1),
        Favorite=bool(u.get('favorite')), HeroDungeonFavorite=False,
        RegDate=_dt(u.get('reg_date')),
        SelectSpecialSkillID=u.get('special_skill', 0),
        vecSpecialSkill=u.get('special_skills', []),
        vecAwakenInfo=[TYPES['NGAwakenInfo'](UnitUID=u['uid'], AwakenID=int(n), State=1)
                       for n in u.get('awaken', [])],
        vecEquipInfo=equip_info(u),
        AwakenRewardGrade=u.get('awaken_grade', 0),
        TotalPower=u.get('power', u.get('level', 1) * 100),
        currentRareness=u.get('rareness', 0),
    )


def worn_relics(player):
    """{unit uid: [artifact, ...]} for every artifact the player has equipped.

    Named for the UI's word at the time; these are Artifacts (ResourceType 8),
    not the Scene Cards the UI now calls Relics.  `worn_scenecards` is those.
    """
    out = {}
    for a in player.artifacts():
        uid = int(a.get('equip', 0) or 0)
        if uid:
            out.setdefault(uid, []).append(a)
    return out


def worn_scenecards(player):
    """{unit uid: [relic, ...]} for every Scene Card the player is wearing."""
    out = {}
    for c in player.scenecards():
        uid = int(c.get('equip', 0) or 0)
        if uid:
            out.setdefault(uid, []).append(c)
    return out


def unit_infos(player, units=None):
    """`unit_info` for many of a player's units, relics attached.

    Use this instead of a bare `unit_info` comprehension anywhere a *player's*
    units go to a client, or the hero will show an equipped relic in
    vecEquipInfo with no relic data behind it.
    """
    worn = worn_relics(player)
    cards = worn_scenecards(player)
    rows = player.d['units'] if units is None else units
    return [unit_info(dict(u, scenecards=cards.get(int(u['uid']), ())),
                      worn.get(int(u['uid']), ()))
            for u in rows]


def party_info(p):
    return TYPES['NGPartyInfo'](
        SlotType=p.get('slot_type', 0), SlotIndex=p.get('slot_index', 0),
        UnitUID=p.get('unit_uid', 0), SkillOnOff=p.get('skill_on_off', 0))


# SeasonType, straight off the client's own enum @dump.cs:651196.
SEASON_DAILY, SEASON_WEEKLY = 1, 2
SEASON_GUILD_WARS, SEASON_GUILD_RAID = 4, 5
SEASON_WORLD_ARENA, SEASON_ARENA, SEASON_TAG_ARENA = 6, 7, 8
SEASON_MONTHLY = 9


def season_values():
    """NGCheckServerInfo.vecSeasonValue -- which seasons are running.

    Not decoration.  `NMUserInfo.CheckFindNewArenaMatchUserReq` @0x13B007C
    calls `GetNGSeasonValue(SeasonType.Arena)` and bails to its error path when
    it comes back null, and `ArenaMatchPlayer.StartMatch` @0x1FAF560 refuses to
    send anything unless that check is clean.  With no season the arena screen
    opens, lists opponents, accepts a team -- and the Fight button silently
    does nothing, because the client never talks to us at all.

    Retail ran these on a real calendar.  There is no calendar here, so the
    window is simply always open.
    """
    from ..settings import SETTINGS
    from datetime import timedelta
    now = datetime.utcnow()
    start, end = now - timedelta(days=30), now + timedelta(days=365)
    season = int(SETTINGS.get('arena.season', 1))
    return [TYPES['NGSeasonValue'](iSeasonType=t, iSeasonValue=season,
                                   tmStartDate=start, tmEndDate=end)
            for t in (SEASON_DAILY, SEASON_WEEKLY, SEASON_MONTHLY,
                      SEASON_ARENA, SEASON_TAG_ARENA, SEASON_WORLD_ARENA,
                      SEASON_GUILD_WARS, SEASON_GUILD_RAID)]


def check_info(**kw):
    """An NGCheckServerInfo with only the named lists populated.

    vecSeasonValue is the exception: it goes in every one of these unless the
    caller overrides it, because the client gates features on a season being
    open and there is no single packet that establishes them.
    """
    info = TYPES['NGCheckServerInfo']()
    kw.setdefault('vecSeasonValue', season_values())
    for k, v in kw.items():
        setattr(info, k, v)
    return info


def resource_sync(player, only=None, **extra):
    """The common case: 'here is your new currency state, plus whatever else'.

    With no explicit key set this sends everything the player has spent or
    earned since the last sync.  That matters because a stage deducts stamina
    at DungeonStart -- whose Ack has no NGCheckServerInfo slot -- and the
    deduction only reaches the client on the next packet that does.
    """
    if only is None:
        only = player.take_dirty() or None
    cols = player.take_dirty_collections()
    if cols and 'vecAddCollectionInfo' not in extra:
        extra['vecAddCollectionInfo'] = collection_infos(player, cols)
    return check_info(vecAddResourceInfo=resource_infos(player, only), **extra)
