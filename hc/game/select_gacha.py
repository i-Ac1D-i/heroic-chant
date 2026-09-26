"""The Selective Cube: reroll a 10-hero summon as often as you like, claim once.

The client's own text says what it is (strings 34523-34529):

* "Selective Cube guarantees 1 SS hero in x10 rolls, and you can reroll until
  you stop and claim the heroes."
* "Up to one SS Hero will appear." / "Up to three S Heroes will appear."
* "Once you tap 'Claim', you will get the summoned heroes immediately and be
  no longer available to open Selective Cube."
* "Selective Cube results won't apply to missions."

and `GachaSelectSummon` holds the numbers: GachaID 1000 opens after stage
``OpenDungeonID`` (5), ``ResultType_0`` (SS) appears exactly once and
``ResultType_1`` (S) one to three times; the rest are ``ResultType_2`` (A).
Those are the banner's ``unitGachaID`` rows in order, the same positional
mapping the ordinary summon uses (hc/game/gacha.py).  ``GachaList.ReSummonMinute``
(525 600, a year) is how long a claim blocks the next one.

Packets: ``SelectGachaInstantSummonReq`` (30269) rolls -- the Ack's
``vecSelectReward`` is the ten heroes as ResourceType 1 (Unit) / unit id, which
``SelectGachaInstantSummonAck`` @0x1505228 walks with ``GetUnitInfo`` to pick
the reveal -- and ``SelectGachaRewardReq`` (30270) claims the last roll.  The
claim goes out as ``NGSelectGacha`` (GachaID, RecvTime), also sent at login in
``NGLogInAck03.vecSelectGacha``.

How the S count within 1..3 is decided is INFERRED: the nine non-SS slots are
rolled with the elite group's S/A weights (18% / 72%) and the count is then
clamped into range.
"""
import random
from datetime import datetime, timedelta

from ..data.tables import TABLES, to_int
from ..protocol.dto import TYPES

ROLL_SIZE = 10

# The client's messages for this (errorString -900..-904).
INFO_MISSING = -900        # "Necessary information for Selective Summon is missing."
NOT_OPEN = -901            # "Necessary condition for Selective Summon isn't achieved."
ALREADY_CLAIMED = -902     # "You can claim Heroes from Selective Summon only once."
NO_ROLL = -903             # "Selective Summon information is missing."
NO_REWARD = -904           # "Reward information of Selective Summon is missing."


def row(gacha_id):
    return TABLES.row('GachaSelectSummon', 'GachaID', int(gacha_id), source='sql')


def _limits(r, result_type):
    lo = to_int(r.get('ResultType_%d_MinCount' % result_type), 0)
    hi = to_int(r.get('ResultType_%d_MaxCount' % result_type), ROLL_SIZE)
    return max(lo, 0), max(hi, lo)


def _state(player):
    return player.d.setdefault('select_gacha', {})


def claimed_at(player, gacha_id):
    try:
        return datetime.fromisoformat(_state(player).get(str(int(gacha_id)), {})['claimed'])
    except (KeyError, TypeError, ValueError):
        return None


def blocked_until(player, gacha_id):
    """When the claim stops blocking a new one, or None if not claimed."""
    when = claimed_at(player, gacha_id)
    if when is None:
        return None
    lst = TABLES.row('GachaList', 'GachaID', int(gacha_id), source='sql') or {}
    minutes = to_int(lst.get('ReSummonMinute'), -1)
    if minutes <= 0:
        return datetime.max
    return when + timedelta(minutes=minutes)


def can_open(player, gacha_id, now=None):
    """(error or 0) for rolling this cube now."""
    r = row(gacha_id)
    if r is None or not TABLES.rows('unitGachaID', 'GachaID', int(gacha_id), source='json'):
        return INFO_MISSING
    need = to_int(r.get('OpenDungeonID'), -1)
    if need > 0 and str(need) not in player.d.get('cleared', {}):
        return NOT_OPEN
    until = blocked_until(player, gacha_id)
    if until is not None and (now or datetime.utcnow()) < until:
        return ALREADY_CLAIMED
    return 0


def roll(gacha_id, rng=random):
    """Ten (unit_id, rareness) pairs: one SS, one to three S, the rest A."""
    r = row(gacha_id)
    tiers = TABLES.rows('unitGachaID', 'GachaID', int(gacha_id), source='json')
    if r is None or len(tiers) < 3:
        return []
    ss_lo, ss_hi = _limits(r, 0)
    s_lo, s_hi = _limits(r, 1)
    ss = max(ss_lo, min(ss_hi, 1))

    weights = [180000, 720000]
    info = TABLES.row('GachaInfo', 'GachaID', int(gacha_id), source='sql') or {}
    group = to_int(info.get('EliteSummonGroup'), -1)
    for g in TABLES.sql('GachaSummonGroupInfo'):
        if to_int(g['SummonGroup']) == group:
            w = [to_int(g.get('ResultType_1'), 0), to_int(g.get('ResultType_2'), 0)]
            if sum(w) > 0:
                weights = w
            break
    rest = ROLL_SIZE - ss
    s_count = sum(1 for _ in range(rest)
                  if rng.choices((1, 2), weights=weights, k=1)[0] == 1)
    s_count = max(s_lo, min(s_hi, s_count))
    counts = {0: ss, 1: s_count, 2: rest - s_count}

    out = []
    for result_type in (0, 1, 2):
        tier = tiers[result_type]
        pool = [to_int(p['UnitID'])
                for p in TABLES.rows('unitRateGroup', 'GroupID', to_int(tier['GroupID']),
                                     source='json')]
        if not pool:
            continue
        for _ in range(counts[result_type]):
            out.append((rng.choice(pool), to_int(tier.get('Rareness'), 0)))
    rng.shuffle(out)
    return out


def remember_roll(player, gacha_id, results, now=None):
    rec = _state(player).setdefault(str(int(gacha_id)), {})
    rec['roll'] = [[int(u), int(r)] for u, r in results]
    rec['rolled'] = (now or datetime.utcnow()).isoformat(timespec='seconds')


def pending(player, gacha_id):
    return [(int(u), int(r)) for u, r in
            _state(player).get(str(int(gacha_id)), {}).get('roll') or []]


def mark_claimed(player, gacha_id, now=None):
    rec = _state(player).setdefault(str(int(gacha_id)), {})
    rec['claimed'] = (now or datetime.utcnow()).isoformat(timespec='seconds')
    rec['roll'] = []


def infos(player):
    """NGSelectGacha for every claimed cube -- for the login and the claim Ack."""
    out = []
    for gid in sorted(_state(player), key=int):
        when = claimed_at(player, gid)
        if when is not None:
            out.append(TYPES['NGSelectGacha'](GachaID=int(gid), RecvTime=when))
    return out
