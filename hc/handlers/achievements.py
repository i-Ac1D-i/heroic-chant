"""Achievement rewards."""
import logging

from ..net import handler
from ..game import state, rewards, achievements
from ..game.errors import Err

log = logging.getLogger('hc.achievements')


@handler(30129)
async def get_achievement_reward(s, a):
    p = s.player
    claimed = achievements.claimed(p)
    granted = []
    for aid in a['_vecAchievementID']:
        aid = int(aid)
        if aid in claimed:
            continue
        row = achievements.row(aid)
        if row is None or not achievements.complete(p, row):
            continue
        rewards.grant(p, achievements.reward(row))
        claimed.append(aid)
        granted.append(aid)
    if not granted:
        await s.send(40147, Err.INVALID, a['_vecAchievementID'], state.resource_sync(p))
        return
    p.save()
    await s.send(40147, Err.OK, granted, state.resource_sync(p))
