"""Login sequence: LogInReq -> LogInAck01..05 + LoginAckLargeData."""
import logging
from datetime import datetime

from .. import config
from ..net import handler
from ..protocol.dto import TYPES
from ..game import state, gacha, guild, shop, artifacts, scenecards
from ..game.errors import Err
from ..game.player import Player
from .center import account_for_device

log = logging.getLogger('hc.login')


def _ack01(player):
    now = datetime.utcnow()
    commanders = [TYPES['NGCommanderInfo'](ID=c['id'], Level=c['level'], Tier=c['tier'])
                  for c in player.d.get('commanders', [])]
    return TYPES['NGLogInAck01'](
        Error=0,
        Nickname=player.nickname,
        AuthType=20,
        Exp=player.d['exp'],
        PurchaseAgeLimit=now,
        LastLoginDate=now,
        RegDate=state._dt(player.d['reg_date']),
        vecCommanderInfo=commanders,
        IsJoin=True,
        RepresentProfile=player.d['represent_profile'],
        vecUserAllFrames=state.frame_infos(player),
        vecTutorialID=player.d.get('tutorials', []),
        vecDimensionGacha=gacha.dimension_gacha(player),
        ngGuildMember=guild.guild_member_dto(player),
        vecAchievementComplete=[],
        WallPaperID=player.d.get('wallpaper_id', 0),
        TeamType=0,
        # The client opens a second connection for PvP; point it back at us so
        # it never tries to reach the dead retail match servers.
        matchHostName=config.PUBLIC_HOST,
        matchPort=config.PORT,
    )


def _large_data(player):
    return TYPES['NGLoginAckLargeData'](
        vecResource=state.resource_infos(player),
        vecCollectionInfo=state.collection_infos(player),
        # Relics live here, not in the wallet -- they are per-instance objects
        # with their own UIDs.  Without this the relic screen is empty and
        # nothing can be equipped.
        vecArtifactInfo=artifacts.infos(player),
        # Relics.  Same story as artifacts: per-instance, own UIDs, and the
        # relic screen is empty without them.
        vecSceneCardInfo=scenecards.infos(player),
        vecUnit=state.unit_infos(player),
        vecPartyInfo=[state.party_info(p) for p in player.d.get('party', [])],
        vecShopGoods=shop.all_shop_goods(player),
        bEndPacket=True,
    )


@handler(30000)
async def login(s, a):
    device_id = a['DeviceID']
    account_id = a['AccountID'] or account_for_device(device_id)
    player = Player.load(account_id) or Player.create(account_id, device_id)
    player.d['last_login'] = datetime.utcnow().isoformat(timespec='seconds')
    # A summon cube the client never opened would otherwise sit in
    # NGDimensionGacha.vecSummon forever and block every future summon behind
    # the client's "previous progress was not complete" check.  The hero was
    # already granted when it was rolled, so nothing is lost by dropping it.
    player.clear_gacha_results()
    player.save()
    s.account, s.player = account_id, player

    log.info('login: account %d (%s) client %s, %d units',
             account_id, player.nickname, a['ClientVersion'], len(player.d['units']))

    await s.send(40000, _ack01(player))
    await s.send(40001, TYPES['NGLogInAck02']())
    # vecAwakenStat is the account's claimed Awakening Passive Mastery stats.
    # Without it every one of them reads as unclaimed after a relogin, and
    # rank-up -- which is gated on the stat, not on the star count -- locks
    # itself again.
    await s.send(40002, TYPES['NGLogInAck03'](
        vecAwakenStat=[TYPES['NGAwakenStat'](AwakenStatID=int(sid), State=1)
                       for sid in player.awaken_stats()]))
    await s.send(40003, TYPES['NGLogInAck04'](customStringData=''))
    shop_info, shop_goods_info, count_price = shop.shop_tables()
    await s.send(40004, TYPES['NGLogInAck05'](
        vecShopInfo=shop_info, vecShopGoodsInfo=shop_goods_info,
        vecCountPrice=count_price))
    await s.send(40005, _large_data(player))


@handler(30022)
async def check_server_info(s, a):
    if s.player is None:
        return
    await s.send(40027, Err.OK, datetime.utcnow(), state.resource_sync(s.player))


@handler(30052)
async def check_refresh_resource(s, a):
    if s.player is None:
        return
    await s.send(40057, Err.OK, state.resource_sync(s.player))
