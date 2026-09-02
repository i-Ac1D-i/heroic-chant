"""Guilds.

This server has one player per account and no cross-account state, so a guild
here is a **guild of one**: you found it, you own it, and you are its only
member.  That is enough to light up the whole guild UI -- creation, info,
comments, donations, the buff board and the exchange shop -- without inventing
a social layer the server cannot back.

Everything comes from the shipped tables:

* ``GuildInfo``      -- one row: creation cost, donation size/reward/daily cap
* ``GuildLevelInfo`` -- guild level thresholds and member caps
* ``GuildBuffInfo``  -- the buff board, gated on guild level, priced in gold
* ``GuildExchange``  -- the exchange shop (Shards -> Guild Coin, etc.)

Anything needing other players -- wars, PvP, raids, invites, applications --
is deliberately out of scope; those handlers answer politely and empty.
"""
from datetime import datetime, timedelta

from ..data.tables import TABLES, to_int
from ..protocol.dto import TYPES
from . import state


def info():
    rows = TABLES.sql('GuildInfo')
    return rows[0] if rows else {}


def create_cost():
    return to_int(info().get('CreateGold'), 1_000_000)


def join_rank_required():
    return to_int(info().get('JoinLevel'), 10)


def donation_gold():
    return to_int(info().get('DonationGold'), 100_000)


def donation_reward():
    return to_int(info().get('DonationReward'), 10)


def donation_cap():
    return to_int(info().get('DonationCount'), 5)


def level_for(contribution):
    """Guild level from accumulated contribution, per GuildLevelInfo."""
    level = 1
    for row in sorted(TABLES.sql('GuildLevelInfo'),
                      key=lambda r: to_int(r['GuildLevel'])):
        if int(contribution) >= to_int(row.get('NeedExp'), 0):
            level = to_int(row['GuildLevel'], 1)
        else:
            break
    return level


def member_cap(level):
    row = TABLES.row('GuildLevelInfo', 'GuildLevel', level)
    return to_int(row.get('MaxCount'), 20) if row else 20


def buff(buff_uid):
    return TABLES.row('GuildBuffInfo', 'BuffUID', buff_uid)


def buffs_for_passive(passive_id):
    """All levels of one buff line, lowest first."""
    return sorted((r for r in TABLES.sql('GuildBuffInfo')
                   if to_int(r['PassiveID']) == int(passive_id)),
                  key=lambda r: to_int(r.get('BuffLevel'), 0))


def next_buff_level(passive_id, current_level):
    for row in buffs_for_passive(passive_id):
        if to_int(row.get('BuffLevel'), 0) == int(current_level) + 1:
            return row
    return None


def buff_expiry(row, now=None):
    """BuffTime is in minutes."""
    minutes = to_int(row.get('BuffTime'), 0)
    return (now or datetime.utcnow()) + timedelta(minutes=max(minutes, 0))


def exchange_entries():
    """GuildExchange rows as {uid, cost, reward, max} dicts.

    Cost is `ExchangeCount` of (ResourceType1, ResourceType2); the reward is
    `RewardValue` of (RewardType1, RewardType2) -- typically Shards in, Guild
    Coin out.
    """
    out = []
    for i, r in enumerate(TABLES.json('GuildExchange')
                          if 'GuildExchange' not in TABLES.sql_tables()
                          else TABLES.sql('GuildExchange')):
        out.append({
            'uid': i + 1,
            'cost': (to_int(r['ResourceType1']), to_int(r['ResourceType2'], -1),
                     to_int(r.get('ExchangeCount'), 1)),
            'reward': (to_int(r['RewardType1']), to_int(r['RewardType2'], -1),
                       to_int(r.get('RewardValue'), 0)),
            'max': to_int(r.get('ExchangeMaxCount'), 0),
        })
    return out


# ------------------------------------------------------------------- DTOs --


def guild_dto(player):
    """NGGuild / NGGuildBase -- they serialise identically, so one builder does
    for both."""
    g = player.d.get('guild')
    if not g:
        return TYPES['NGGuild']()
    now = datetime.utcnow()
    buffs = []
    for passive, b in (g.get('buffs') or {}).items():
        try:
            until = datetime.fromisoformat(b['until'])
        except (KeyError, TypeError, ValueError):
            continue
        if until > now:
            buffs.append(TYPES['NGGuildBuff'](
                BuffUID=int(b.get('buff_uid', 0)), PassiveID=int(passive),
                DurationTime=until))
    return TYPES['NGGuild'](
        UID=int(g['uid']), Name=g['name'], Introduction=g.get('introduction', ''),
        MarkFlag=int(g.get('mark_flag', 0)),
        MarkColorIndex=int(g.get('mark_color', 0)),
        JoinRank=int(g.get('join_rank', 1)), JoinType=int(g.get('join_type', 0)),
        Contribution=int(g.get('contribution', 0)),
        Donation=int(g.get('donation', 0)),
        MemberCount=1, vecBuff=buffs,
        AttendanceCount=int(g.get('attendance', 0)), DailySeason=0,
        Comment=g.get('comment', ''), vecRaid=[],
        ShopLevel=int(g.get('shop_level', 1)),
        RaidRewardPoint=0, RaidClearCount=0,
        tmRaidLastClearTime=state._dt(g.get('created')),
        tmCreateTime=state._dt(g.get('created')))


def guild_member_dto(player):
    g = player.d.get('guild')
    if not g:
        return TYPES['NGGuildMember']()
    m = g.get('member', {})
    return TYPES['NGGuildMember'](
        AccountID=player.account_id, GuildUID=int(g['uid']),
        TitleUnitID=int(m.get('title_unit', 0)),
        MemberState=1,                       # founder / master
        NickName=player.nickname, UserRank=player.rank(),
        Donation=int(m.get('donation', 0)),
        Contribution=int(m.get('contribution', 0)),
        WeeklyContriubution=int(m.get('contribution', 0)), WeeklySeason=0,
        tmLastLogIn=datetime.utcnow(), tmLastDonation=datetime.utcnow(),
        Comment=m.get('comment', ''), RaidTicketCount=0,
        DonationCount=int(g.get('donation_count', 0)), ConnectState=1,
        SkinID=player.d.get('skin_id', 0), DailySeason=0, GuildWarsSeason=0,
        frameInfo=TYPES['NGFrameInfo']())


def guild_shop_goods(player, uid):
    g = player.d.get('guild') or {}
    return TYPES['NGGuildShopGoods'](
        GoodsUID=int(uid),
        BuyCount=int((g.get('shop_buys') or {}).get(str(uid), 0)),
        BuySeason=0, tmRegDate=datetime.utcnow())


def guild_shop(player):
    """(vecGoodsInfo, vecGoodsScript) built from GuildExchange."""
    goods, script = [], []
    level = int((player.d.get('guild') or {}).get('shop_level', 1))
    for entry in exchange_entries():
        ct1, ct2, cval = entry['cost']
        rt1, rt2, rval = entry['reward']
        goods.append(guild_shop_goods(player, entry['uid']))
        script.append(TYPES['NGGuildShopGoodsScript'](
            GoodsUID=entry['uid'], PeriodType=0,
            MaxBuyCount=entry['max'], OpenGuildshopLevel=1,
            ItemInfo=state.resource_info(rt1, rt2, -1, rval),
            CostInfo=state.resource_info(ct1, ct2, -1, cval)))
    return goods, script
