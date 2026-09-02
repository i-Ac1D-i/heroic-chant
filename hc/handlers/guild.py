"""Guild: creation, info, comments, donations, buffs and the exchange shop.

See hc/game/guild.py for why this is a guild of one.  Handlers that would need
other players answer with an empty, well-formed reply rather than nothing, so
the client's screens open instead of hanging.
"""
import logging
from datetime import datetime

from ..net import handler
from ..protocol.dto import TYPES
from ..game import state, guild
from ..game.errors import Err
from ..game.enums import ResourceType

log = logging.getLogger('hc.guild')


@handler(30082)
async def create_guild(s, a):
    p = s.player
    req = a['_Guild']
    if p.d.get('guild'):
        await s.send(40090, Err.NOT_FOUND, guild.guild_dto(p), state.resource_sync(p))
        return
    if p.rank() < guild.join_rank_required():
        log.info('rank %d too low to found a guild (needs %d)',
                 p.rank(), guild.join_rank_required())
        await s.send(40090, Err.INVALID, TYPES['NGGuild'](), state.resource_sync(p))
        return
    cost = guild.create_cost()
    if not p.spend_resource(ResourceType.Gold, cost):
        log.info('cannot afford guild creation (%d gold)', cost)
        await s.send(40090, Err.NOT_ENOUGH, TYPES['NGGuild'](), state.resource_sync(p))
        return

    p.d['guild'] = {
        'uid': 1000 + p.account_id,
        'name': req.Name or ('%s\'s Guild' % p.nickname),
        'introduction': req.Introduction or '',
        'mark_flag': int(req.MarkFlag), 'mark_color': int(req.MarkColorIndex),
        'join_rank': int(req.JoinRank), 'join_type': int(req.JoinType),
        'contribution': 0, 'donation': 0, 'comment': '', 'shop_level': 1,
        'created': datetime.utcnow().isoformat(timespec='seconds'),
        'buffs': {}, 'shop_buys': {}, 'donation_count': 0, 'donation_day': '',
        'member': {'donation': 0, 'contribution': 0, 'comment': '', 'title_unit': 0},
    }
    p.save()
    log.info('guild founded: %r (uid %d) for %d gold',
             p.d['guild']['name'], p.d['guild']['uid'], cost)
    await s.send(40090, Err.OK, guild.guild_dto(p), state.resource_sync(p))


@handler(30083)
async def get_guild_info(s, a):
    p = s.player
    if not p.d.get('guild'):
        await s.send(40091, Err.NOT_FOUND, TYPES['NGGuild'](), TYPES['NGGuildMember'](),
                     state.resource_sync(p))
        return
    await s.send(40091, Err.OK, guild.guild_dto(p), guild.guild_member_dto(p),
                 state.resource_sync(p))


@handler(30084)
async def recom_guild(s, a):
    """Recommended guilds -- nothing to recommend on a single-account server."""
    await s.send(40092, Err.OK, [])


@handler(30085)
async def find_guild(s, a):
    await s.send(40093, Err.OK, [])


@handler(30097)
async def get_invite_guild_info(s, a):
    await s.send(40108, Err.OK, [])


@handler(30092)
async def change_guild_info(s, a):
    p = s.player
    g = p.d.get('guild')
    if not g:
        await s.send(40103, Err.NOT_FOUND, TYPES['NGGuildBase']())
        return
    req = a['_Guild']
    g['name'] = req.Name or g['name']
    g['introduction'] = req.Introduction
    g['mark_flag'] = int(req.MarkFlag)
    g['mark_color'] = int(req.MarkColorIndex)
    g['join_rank'] = int(req.JoinRank)
    g['join_type'] = int(req.JoinType)
    p.save()
    log.info('guild info updated: %r', g['name'])
    await s.send(40103, Err.OK, guild.guild_dto(p))


@handler(30140)
async def change_guild_comment(s, a):
    p = s.player
    if p.d.get('guild'):
        p.d['guild']['comment'] = a['_Comment']
        p.save()
    await s.send(40157, Err.OK, a['_Comment'])


@handler(30141)
async def change_guild_member_comment(s, a):
    p = s.player
    if p.d.get('guild'):
        p.d['guild']['member']['comment'] = a['_Comment']
        p.save()
    await s.send(40158, Err.OK, a['_Comment'])


@handler(30095)
async def guild_donation(s, a):
    """Donate gold for guild contribution and Guild Coin, capped per day."""
    p = s.player
    g = p.d.get('guild')
    if not g:
        await s.send(40106, Err.NOT_FOUND, TYPES['NGGuildBase'](), TYPES['NGGuildMember'](),
                     state.resource_sync(p))
        return

    today = datetime.utcnow().date().isoformat()
    if g.get('donation_day') != today:
        g['donation_day'], g['donation_count'] = today, 0
    if g['donation_count'] >= guild.donation_cap():
        log.info('donation cap reached (%d/day)', guild.donation_cap())
        await s.send(40106, Err.INVALID, guild.guild_dto(p), guild.guild_member_dto(p),
                     state.resource_sync(p))
        return

    cost = guild.donation_gold()
    if not p.spend_resource(ResourceType.Gold, cost):
        await s.send(40106, Err.NOT_ENOUGH, guild.guild_dto(p), guild.guild_member_dto(p),
                     state.resource_sync(p))
        return

    reward = guild.donation_reward()
    g['donation_count'] += 1
    g['donation'] = int(g.get('donation', 0)) + cost
    g['contribution'] = int(g.get('contribution', 0)) + reward
    g['member']['donation'] = int(g['member'].get('donation', 0)) + cost
    g['member']['contribution'] = int(g['member'].get('contribution', 0)) + reward
    p.add_resource(ResourceType.GuildCoin, reward)
    p.save()
    log.info('donated %d gold -> +%d contribution, guild level %d',
             cost, reward, guild.level_for(g['contribution']))
    await s.send(40106, Err.OK, guild.guild_dto(p), guild.guild_member_dto(p),
                 state.resource_sync(p))


@handler(30091)
async def withdraw_guild(s, a):
    p = s.player
    member = guild.guild_member_dto(p)
    p.d.pop('guild', None)
    p.save()
    log.info('left the guild')
    await s.send(40102, Err.OK, member)


# ------------------------------------------------------------------- buffs --
@handler(30210)
async def guild_buff_levelup(s, a):
    p = s.player
    g = p.d.get('guild')
    if not g:
        await s.send(40229, Err.NOT_FOUND, TYPES['NGGuildBase']())
        return
    passive = int(a['_PassiveID'])
    buffs = g.setdefault('buffs', {})
    cur = int(buffs.get(str(passive), {}).get('level', 0))
    row = guild.next_buff_level(passive, cur)
    if row is None:
        await s.send(40229, Err.INVALID, guild.guild_dto(p))
        return
    if guild.level_for(g.get('contribution', 0)) < guild.to_int(row.get('OpenGuildLevel'), 1):
        log.info('guild level too low for buff %s level %s',
                 passive, row.get('BuffLevel'))
        await s.send(40229, Err.LOCKED, guild.guild_dto(p))
        return
    cost = guild.to_int(row.get('Cost'), 0)
    if cost and not p.spend_resource(ResourceType.Gold, cost):
        await s.send(40229, Err.NOT_ENOUGH, guild.guild_dto(p))
        return
    buffs[str(passive)] = {
        'level': cur + 1,
        'buff_uid': guild.to_int(row.get('BuffUID'), 0),
        'until': guild.buff_expiry(row).isoformat(timespec='seconds'),
    }
    p.save()
    log.info('guild buff %d -> level %d (%d gold)', passive, cur + 1, cost)
    await s.send(40229, Err.OK, guild.guild_dto(p))


@handler(30137)
async def buy_guild_buff(s, a):
    """Extend an already-unlocked buff's duration."""
    p = s.player
    g = p.d.get('guild')
    if not g:
        await s.send(40154, Err.NOT_FOUND, TYPES['NGGuildBase']())
        return
    passive = int(a['_PassiveID'])
    entry = g.setdefault('buffs', {}).get(str(passive))
    row = guild.buff(entry['buff_uid']) if entry else None
    if row is None:
        await s.send(40154, Err.INVALID, guild.guild_dto(p))
        return
    cost = guild.to_int(row.get('BuyTimeCost'), 0) * max(int(a['_buyCount']), 1)
    if cost and not p.spend_resource(ResourceType.Gold, cost):
        await s.send(40154, Err.NOT_ENOUGH, guild.guild_dto(p))
        return
    entry['until'] = guild.buff_expiry(row).isoformat(timespec='seconds')
    p.save()
    await s.send(40154, Err.OK, guild.guild_dto(p))


# -------------------------------------------------------------- guild shop --
@handler(30187)
async def get_guild_shop_info(s, a):
    goods, script = guild.guild_shop(s.player)
    log.info('guild shop: %d listings', len(script))
    await s.send(40203, Err.OK, goods, script)


@handler(30188)
async def levelup_guild_shop(s, a):
    p = s.player
    g = p.d.get('guild')
    if not g:
        await s.send(40204, Err.NOT_FOUND, TYPES['NGGuildBase'](), [], [])
        return
    g['shop_level'] = int(g.get('shop_level', 1)) + 1
    p.save()
    goods, script = guild.guild_shop(p)
    log.info('guild shop level -> %d', g['shop_level'])
    await s.send(40204, Err.OK, guild.guild_dto(p), goods, script)


@handler(30189)
async def buy_guild_shop_goods(s, a):
    p = s.player
    g = p.d.get('guild')
    uid = int(a['_GoodsUID'])
    entry = next((x for x in guild.exchange_entries() if x['uid'] == uid), None)
    if not g or entry is None:
        await s.send(40205, Err.NOT_FOUND, TYPES['NGGuildShopGoods'](), state.resource_sync(p))
        return

    bought = int(g.setdefault('shop_buys', {}).get(str(uid), 0))
    if entry['max'] and bought >= entry['max']:
        await s.send(40205, Err.INVALID, guild.guild_shop_goods(p, uid), state.resource_sync(p))
        return

    ct1, ct2, cval = entry['cost']
    if not p.spend_resource(ct1, cval, ct2):
        await s.send(40205, Err.NOT_ENOUGH, guild.guild_shop_goods(p, uid), state.resource_sync(p))
        return
    rt1, rt2, rval = entry['reward']
    p.add_resource(rt1, rval, rt2)
    g['shop_buys'][str(uid)] = bought + 1
    p.save()
    log.info('guild exchange %d: paid %d of %d/%d -> %d of %d/%d',
             uid, cval, ct1, ct2, rval, rt1, rt2)
    await s.send(40205, Err.OK, guild.guild_shop_goods(p, uid), state.resource_sync(p))


# --------------------------------------------------- raids / wars (stubbed) --
@handler(30208)
async def check_guild_raid_info(s, a):
    await s.send(40227, Err.OK, TYPES['NGRankingInfo'](), [], state.resource_sync(s.player))


@handler(30251)
async def check_guild_wars_info(s, a):
    await s.send(40274, Err.OK, guild.guild_dto(s.player), TYPES['NGGuildBase'](),
                 state.resource_sync(s.player), [], TYPES['NGGuildWarsPlay'](), False)


@handler(30363)
async def guild_mission_total_count(s, a):
    await s.send(40386, Err.OK)
