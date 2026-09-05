"""Stage reward rolls, driven by the client's own rewardClear table.

Reward rows carry ``dropGroup`` and ``frequency``.  Rows with
``dropGroup == -1`` are unconditional.  Rows sharing a dropGroup form a
weighted pool from which exactly one row is drawn -- the weights in a group
sum to well over 1000 (e.g. 210+300+650 for stage 2 group 10), so they cannot
each be an independent per-mille chance.  The retail drop logic lived on the
server and is not recoverable, so this is a reconstruction; it is the single
place to change if you want different drop behaviour.
"""
import random
from collections import defaultdict

from ..data.tables import TABLES, to_int
from ..settings import SETTINGS


def roll(dungeon_id, first_clear, rng=random):
    """Return [(type1, type2, type3, amount), ...] for one clear of a stage."""
    over = SETTINGS.get('rewards.stage_overrides', {}).get(str(int(dungeon_id)))
    if over is not None:
        return _scaled([tuple(int(x) for x in r) for r in over], first_clear)
    rows = [r for r in TABLES.clear_rewards(dungeon_id)
            if bool(to_int(r['firstClear'], 0)) == bool(first_clear)]
    out, groups = [], defaultdict(list)
    for r in rows:
        g = to_int(r['dropGroup'], -1)
        if g < 0:
            out.append(_as_reward(r))
        else:
            groups[g].append(r)
    for rows_in_group in groups.values():
        weights = [max(to_int(r['frequency'], 0), 0) for r in rows_in_group]
        if sum(weights) <= 0:
            continue
        out.append(_as_reward(rng.choices(rows_in_group, weights=weights, k=1)[0]))
    return _scaled(out, first_clear)


def _scaled(rewards, first_clear):
    """Apply the dashboard's drop multipliers.  Amounts stay whole and never
    round a real drop away to nothing."""
    mult = float(SETTINGS.get('rewards.multiplier', 1.0) or 1.0)
    if first_clear:
        mult *= float(SETTINGS.get('rewards.first_clear_multiplier', 1.0) or 1.0)
    if mult == 1.0:
        return rewards
    return [(t1, t2, t3, max(int(round(v * mult)), 1 if v > 0 else 0))
            for t1, t2, t3, v in rewards]


def _as_reward(r):
    return (to_int(r['resource_type1']), to_int(r['resource_type2']),
            to_int(r['resource_type3']), to_int(r['val_1'], 0))


def star_rewards(dungeon_id):
    """One-off rewards for the per-stage star missions."""
    idx = TABLES.index('starSystemReward', 'dungeonID', unique=False)
    return [(to_int(r['rewardType1']), to_int(r['rewardType2']), -1,
             to_int(r['rewardVal1'], 0)) for r in idx.get(int(dungeon_id), [])]


def grant(player, rewards):
    """Apply rewards to a player; returns the set of touched resource keys."""
    touched = set()
    for t1, t2, t3, amount in rewards:
        if amount <= 0:
            continue
        if t1 == 1:                      # ResourceType.Unit -> mint a real unit
            player.add_unit(t2 if t2 > 0 else amount)
            continue
        player.add_resource(t1, amount, t2, t3)
        touched.add((t1, t2, t3))
    return touched
