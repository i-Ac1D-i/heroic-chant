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


def unit_info(u):
    return TYPES['NGUnitInfo'](
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
