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


# ---------------------------------------------------------------- fast reward
# "Speed Acquired": buy City Search loot outright, up to 10 times a day.
# `fastRewardTime` rows 1-10 are the 1st..10th purchase of the day: each pays
# `rewardMinute` (120) minutes of the farm, and costs its own resource --
# 1 000 gold for the first, then 10..100 TotalCash.
#
# Read off the client: PopupboxSpeedAcquired shows the next purchase as
# GetFastRewardTime(FastRewardRecvCount + 1) (init @0x1B7D47C), sums the costs
# of rows recv+1 .. recv+count for its count slider (@0x17025CC) and sends
# GetDungeonAutoPlayFastRewardReq(count).  The only field of
# NGDungeonAutoPlayFastReward any screen reads is FastRewardRecvCount, so the
# daily reset is the server's job.  INFERRED: the day is a UTC day, as for the
# daily missions.

def fast_rows():
    return sorted(TABLES.json('fastRewardTime'),
                  key=lambda r: to_int(r.get('fastRewardTime'), 0))


def fast_max():
    return max((to_int(r.get('fastRewardTime'), 0) for r in fast_rows()), default=0)


def fast_row(n):
    return next((r for r in fast_rows() if to_int(r.get('fastRewardTime'), -1) == int(n)),
                None)


def fast_state(player, now=None):
    """Today's purchase record, reset on a new day."""
    now = now or datetime.utcnow()
    day = now.date().toordinal()
    rec = player.d.setdefault('afk_fast', {})
    if int(rec.get('day', -1)) != day:
        rec.update({'day': day, 'count': 0})
    return rec


def fast_cost(player, count, now=None):
    """[(t1, t2, amount)] for the next `count` purchases, or None if that would
    go past the day's last row."""
    done = int(fast_state(player, now)['count'])
    totals = {}
    for n in range(done + 1, done + int(count) + 1):
        row = fast_row(n)
        if row is None:
            return None
        key = (to_int(row.get('resourceType1'), -1), to_int(row.get('resourceType2'), -1))
        totals[key] = totals.get(key, 0) + to_int(row.get('resourceTypeValue'), 0)
    return [(t1, t2, v) for (t1, t2), v in totals.items() if t1 >= 0 and v > 0]


def fast_minutes(player, count, now=None):
    done = int(fast_state(player, now)['count'])
    return sum(to_int((fast_row(n) or {}).get('rewardMinute'), 0)
               for n in range(done + 1, done + int(count) + 1))


def fast_claim(player, count, now=None):
    """Roll `count` purchases of loot and record them.  Costs are the caller's."""
    now = now or datetime.utcnow()
    minutes = fast_minutes(player, count, now)
    rec = fast_state(player, now)
    rec['count'] = int(rec['count']) + int(count)
    rec['last'] = now.isoformat(timespec='seconds')
    return roll(farm_dungeon(player), minutes / 60.0)


def fast_info_fields(player, now=None):
    """Values for NGDungeonAutoPlayFastReward."""
    now = now or datetime.utcnow()
    rec = fast_state(player, now)
    try:
        last = datetime.fromisoformat(rec.get('last'))
    except (TypeError, ValueError):
        last = datetime(2000, 1, 1)
    return {'Season': int(rec['day']), 'FastRewardRecvCount': int(rec['count']),
            'LastRecvTime': last}


def info_fields(player, now=None):
    """Values for NGDungeonAutoPlayInfo."""
    now = now or datetime.utcnow()
    return {
        'DungeonID': farm_dungeon(player),
        'RewardPeriodHours': PERIOD_HOURS,
        'LastRewardDate': _claimed_at(player),
        'LastUpdateDate': now,
    }
