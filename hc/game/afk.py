"""City Search -- the idle/AFK farm.

The client shows a bar of accumulating rewards with a Claim button. The payout
table is ``rewardTime``: one row per resource per stage, with ``ratio`` out of
10000 as the per-tick chance and ``val_1`` as the amount. The farmed stage is
the furthest one the player has cleared, which is how the live game worked.

``RewardPeriodHours`` caps how long it accrues before it stops, so leaving the
game for a week still only pays a full bar.
"""
import random
from datetime import datetime, timedelta

from ..data.tables import TABLES, to_int

PERIOD_HOURS = 12          # NGDungeonAutoPlayInfo.RewardPeriodHours
TICKS_PER_HOUR = 1


def farm_dungeon(player):
    """The stage City Search runs: the highest dungeon id cleared."""
    cleared = [int(k) for k in player.d.get('cleared', {})]
    if cleared:
        return max(cleared)
    return to_int(player.d.get('last_dungeon'), 1)


def _claimed_at(player):
    try:
        return datetime.fromisoformat(player.d.get('afk_claimed'))
    except (TypeError, ValueError):
        return datetime.utcnow()


def elapsed_hours(player, now=None):
    now = now or datetime.utcnow()
    delta = now - _claimed_at(player)
    hours = delta.total_seconds() / 3600.0
    return max(0.0, min(hours, float(PERIOD_HOURS)))


def preview(player, now=None):
    """[(t1, t2, t3, amount), ...] currently sitting in the bar."""
    hours = elapsed_hours(player, now)
    return roll(farm_dungeon(player), hours)


def roll(dungeon_id, hours, rng=random):
    ticks = int(hours * TICKS_PER_HOUR)
    if ticks <= 0:
        return []
    rows = TABLES.index('rewardTime', 'dungeonID', unique=False).get(int(dungeon_id), [])
    totals = {}
    for _ in range(ticks):
        for r in rows:
            ratio = to_int(r.get('ratio'), 0)
            if ratio <= 0:
                continue
            # ratio is per-10000; 10000 means it always drops.
            if ratio < 10000 and rng.randrange(10000) >= ratio:
                continue
            key = (to_int(r['resource_type1']), to_int(r['resource_type2']),
                   to_int(r['resource_type3']))
            totals[key] = totals.get(key, 0) + to_int(r.get('val_1'), 0)
    return [(k[0], k[1], k[2], v) for k, v in totals.items() if v > 0]


def claim(player, now=None):
    """Pay out the bar and reset the timer.  Returns the rewards granted."""
    now = now or datetime.utcnow()
    rewards = preview(player, now)
    player.d['afk_claimed'] = now.isoformat(timespec='seconds')
    return rewards


def info_fields(player, now=None):
    """Values for NGDungeonAutoPlayInfo."""
    now = now or datetime.utcnow()
    return {
        'DungeonID': farm_dungeon(player),
        'RewardPeriodHours': PERIOD_HOURS,
        'LastRewardDate': _claimed_at(player),
        'LastUpdateDate': now,
    }
