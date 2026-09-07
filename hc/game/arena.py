"""The Arena: asynchronous PvP against other players' defence teams.

Four arena modes exist in the protocol -- Arena, Tag Arena, World Arena and
Global Arena.  Only the first is implemented here, and deliberately: the other
three are real-time and route through the match server, so they need two people
online at the same instant.  Classic Arena is asynchronous.  You attack a
snapshot of somebody's *defence team*, and they never have to be connected.
That is what makes it work on a server with one player.

Opponents come from two places:

* **Other accounts.**  Anyone who has set a defence team is matchable, online
  or not, because the fight is against their saved line-up.
* **Bots.**  The client shipped with 887 of them -- `ArenaAIList` gives the
  name, rating and profile, `ArenaAITeamInfo` the 3,460 rows of team
  composition.  So a one-player server has a full ladder out of the box.
  `arena.bots` in the dashboard adds hand-built teams on top, or replaces the
  shipped ones entirely.

Everything else is read off the client's own tables:

* ``ArenaTierInfo``   32 tiers: the point threshold for each, how far
                      matchmaking may reach (``MatchRange``), how often it
                      should offer a bot (``AiMatchRate``), the point swing for
                      every kind of win and loss, and the per-tier rewards
* ``ArenaMatchGroup`` point band -> match group, which the client displays
* ``ArenaSeasonReward`` end-of-season payout by tier
"""
import logging
import random
import time
from datetime import datetime

from ..data.tables import TABLES, to_int
from ..protocol.dto import TYPES
from ..settings import SETTINGS
from . import state

log = logging.getLogger('hc.arena')

# ContentsID 6 is the arena, per arenaMissionPoint.
CONTENTS_ID = 6

# How many opponents the client keeps on screen.  Retail showed five.
MATCH_SLOTS = 5

# Bot account ids are negative so they can never collide with a real account,
# and so anything that stores "who did I fight" can tell them apart at a glance.
def bot_account_id(index):
    return -abs(int(index))


def is_bot(account_id):
    return int(account_id) < 0


# ------------------------------------------------------------------ tiers --
def tiers():
    """ArenaTierInfo, ascending by the points needed to reach it."""
    rows = TABLES.json('ArenaTierInfo')
    return sorted(rows, key=lambda r: to_int(r.get('PointMin'), 0))


def tier_for(points):
    """The tier row a rating falls in.  Never returns None -- tier 1 is 0+."""
    best = None
    for row in tiers():
        if to_int(row.get('PointMin'), 0) <= int(points):
            best = row
        else:
            break
    return best or tiers()[0]


def tier_number(points):
    return to_int(tier_for(points).get('Tier'), 1)


def tier_name(points):
    row = tier_for(points)
    return TABLES.strings().get(to_int(row.get('TierNameID'), -1)) or (
        'Tier %d' % to_int(row.get('Tier'), 1))


def match_group(points):
    for row in TABLES.json('ArenaMatchGroup'):
        lo = to_int(row.get('StartMatchPoint'), 0)
        hi = to_int(row.get('EndMatchPoint'), 0)
        if lo <= int(points) < hi:
            return to_int(row.get('MatchGroup'), 1)
    return to_int(TABLES.json('ArenaMatchGroup')[-1].get('MatchGroup'), 1)


def point_delta(points, won, opponent_points, opponent_is_bot):
    """How the rating moves after a fight.

    ArenaTierInfo carries a separate win/lose pair for each kind of opponent:
    AiWinPoint/AiLosePoint against a bot, Upper* when the opponent outranks
    you, Lower* when they do not.  Losses are stored as positive numbers in the
    table and subtracted here.
    """
    row = tier_for(points)
    if opponent_is_bot:
        key = 'AiWinPoint' if won else 'AiLosePoint'
    elif int(opponent_points) >= int(points):
        key = 'UpperWinPoint' if won else 'UpperLosePoint'
    else:
        key = 'LowerWinPoint' if won else 'LowerLosePoint'
    amount = to_int(row.get(key), 0)
    return amount if won else -amount


def tier_rewards(points):
    """What one win at this tier pays out: [(t1, t2, t3, amount), ...]."""
    row = tier_for(points)
    out = []
    for n in (1, 2):
        t1 = to_int(row.get('Reward%d_ResourceType1' % n), -1)
        val = to_int(row.get('Reward%d_ResourceVal1' % n), 0)
        if t1 >= 0 and val > 0:
            out.append((t1, to_int(row.get('Reward%d_ResourceType2' % n), -1),
                        -1, val))
    return out


def season_rewards(points):
    t = tier_number(points)
    return [(to_int(r.get('ResourceType1'), -1), to_int(r.get('ResourceType2'), -1),
             -1, to_int(r.get('ResourceVal1'), 0))
            for r in TABLES.json('ArenaSeasonReward')
            if to_int(r.get('Tier'), -1) == t
            and to_int(r.get('ResourceType1'), -1) >= 0]


# ------------------------------------------------------------------- bots --
def _shipped_bots():
    """The 887 AI teams the client came with, indexed by AIIndex."""
    cache = getattr(_shipped_bots, '_cache', None)
    if cache is not None:
        return cache
    teams = {}
    for row in TABLES.json('ArenaAITeamInfo'):
        teams.setdefault(to_int(row.get('AIIndex'), -1), []).append(row)
    out = []
    for row in TABLES.json('ArenaAIList'):
        idx = to_int(row.get('AIIndex'), -1)
        members = teams.get(idx) or []
        if not members:
            continue                       # a profile with no team is unusable
        out.append({
            'index': idx,
            'name': row.get('AINickName') or ('AI %d' % idx),
            'points': to_int(row.get('AIMatchPoint'), 0),
            'exp': to_int(row.get('AIEXP'), 0),
            'profile': to_int(row.get('ProfileID'), 0),
            'commander': to_int(row.get('CommanderID'), 1),
            'commander_level': to_int(row.get('CommanderLevel'), 1),
            'units': [{
                'id': to_int(m.get('UnitID'), 0),
                'level': to_int(m.get('UnitLevel'), 1),
                'tier': to_int(m.get('UnitTier'), 1),
                'grade': to_int(m.get('UnitGrade'), 0),
                'rareness': to_int(m.get('currentRareness'), 0),
                'slot': to_int(m.get('SlotID'), 0),
                'equip': [to_int(m.get('EquipID_%d' % i), -1) for i in (1, 2, 3, 4)],
            } for m in sorted(members, key=lambda r: to_int(r.get('SlotID'), 0))],
            'custom': False,
        })
    _shipped_bots._cache = out
    return out


# ------------------------------------------------------- bot validation ----
# A hand-built team is the one place a person can type numbers straight into
# the battle scene, and the scene is unforgiving: `PlayBaseScene.UnitLoading`
# dereferences whatever the slot maps to and throws a NullReferenceException
# if it is not a real position, which on screen is a black screen with no
# error.  So everything below is checked against the client's own tables
# rather than trusted, and every rule here is one that all 3,460 rows of
# ArenaAITeamInfo already obey -- the strongest evidence available that it is
# what the client expects.

# Party slots are not 0..3.  `EUnitPosition` @dump.cs:633232 is
#   None=-1  ECommander=0  EFront_1=1  EFront_2=2  EBack_1=10 ... EBack_5=14
# so 3 and 4 are not positions at all.  These four are what a real client
# sends in ChangeArenaDefensePartyReq and what SlotID holds in every one of
# the 865 usable shipped teams.  Get this wrong and the hero is missing from
# the preview and takes the battle scene down with it.
DEFAULT_SLOTS = (1, 2, 10, 11)

MAX_TEAM = len(DEFAULT_SLOTS)
GRADE_MAX = 6        # unitGradeStatInfo carries grades 0..6 and nothing else
RARENESS_MAX = 3     # unitRarenessStatInfo carries 0..3
NAME_MAX = 32


def tier_level_caps():
    """{tier: highest level} from TierUpTable -- tier 1 stops at 30, tier 10 at
    110.  Not one shipped AI member exceeds its own tier's cap."""
    cache = getattr(tier_level_caps, '_cache', None)
    if cache is None:
        cache = {}
        for row in TABLES.sql('TierUpTable'):
            tier, lv = to_int(row.get('Tier'), -1), to_int(row.get('MaxLv'), 0)
            if tier >= 0:
                cache[tier] = max(cache.get(tier, 0), lv)
        tier_level_caps._cache = cache
    return cache


def playable_heroes():
    """{unit id: (name, base rareness)} for units a team may contain.

    UnitList holds 768 rows but only 138 are `IsPlayerHero`; the rest are
    bosses, summons and scenery with no playable prefab.  Every hero used by
    the 865 shipped teams is in that 138, and nothing outside it is."""
    cache = getattr(playable_heroes, '_cache', None)
    if cache is None:
        strings = TABLES.strings()
        cache = {}
        for row in TABLES.units():
            if str(row.get('IsPlayerHero')) not in ('1', 'True', 'true'):
                continue
            uid = to_int(row.get('UnitID'), -1)
            if uid < 0:
                continue
            cache[uid] = (strings.get(to_int(row.get('NameID')), str(uid)),
                          to_int(row.get('Rareness'), 0))
        playable_heroes._cache = cache
    return cache


def _clamp(value, low, high):
    return max(low, min(high, value))


def check_bot(bot, index=0):
    """Sanitise one dashboard bot.  Returns (clean bot, [complaints]).

    Repairs rather than rejects wherever a sane value exists, so a team that
    is merely odd still plays; only a member that cannot be placed at all --
    an unknown hero, a duplicate, or one past the fourth -- is dropped.
    """
    problems = []
    heroes = playable_heroes()
    caps = tier_level_caps()
    tier_max = max(caps) if caps else 10

    name = str(bot.get('name') or '').strip()
    if not name:
        name = 'Bot %d' % (index + 1)
        problems.append('the team had no name, so it is called "%s"' % name)
    elif len(name) > NAME_MAX:
        name = name[:NAME_MAX]
        problems.append('the name was trimmed to %d characters' % NAME_MAX)

    raw = list(bot.get('units') or [])
    if len(raw) > MAX_TEAM:
        extra = len(raw) - MAX_TEAM
        problems.append('a team holds %d heroes; the extra %d %s dropped'
                        % (MAX_TEAM, extra, 'was' if extra == 1 else 'were'))
        raw = raw[:MAX_TEAM]

    units, seen = [], set()
    for u in raw:
        if isinstance(u, int):
            u = {'id': u}
        uid = to_int(u.get('id'), -1)
        if uid not in heroes:
            problems.append('hero %s is not a playable hero, so it was dropped'
                            % u.get('id'))
            continue
        hero_name, base_rareness = heroes[uid]
        if uid in seen:
            problems.append('%s was in the team twice; the repeat was dropped'
                            % hero_name)
            continue
        seen.add(uid)

        tier = _clamp(to_int(u.get('tier'), 1), 1, tier_max)
        if tier != to_int(u.get('tier'), 1):
            problems.append('%s: tier must be 1-%d' % (hero_name, tier_max))

        # Level is capped by tier, not globally: tier 1 stops at 30, tier 10 at
        # 110.  Raising the tier is what raises the ceiling.
        level_cap = caps.get(tier, 110)
        level = _clamp(to_int(u.get('level'), 1), 1, level_cap)
        if level != to_int(u.get('level'), 1):
            problems.append('%s: a tier %d hero cannot go past level %d'
                            % (hero_name, tier, level_cap))

        grade = _clamp(to_int(u.get('grade'), 0), 0, GRADE_MAX)
        if grade != to_int(u.get('grade'), 0):
            problems.append('%s: grade must be 0-%d' % (hero_name, GRADE_MAX))

        # Rarity can be raised but never lowered below what the hero is born
        # as -- no shipped member is ever below its unit's own Rareness.
        rareness = _clamp(to_int(u.get('rareness'), base_rareness),
                          base_rareness, RARENESS_MAX)
        if rareness != to_int(u.get('rareness'), base_rareness):
            problems.append('%s: rarity must be %d-%d'
                            % (hero_name, base_rareness, RARENESS_MAX))

        equip = [to_int(e, -1) for e in (u.get('equip') or [])][:4]
        equip += [-1] * (4 - len(equip))

        units.append({
            'id': uid, 'level': level, 'tier': tier, 'grade': grade,
            'rareness': rareness,
            # The slot is assigned, never taken from the input.  It has to be
            # an EUnitPosition -- 1 and 2 are the front row, 10 and 11 the
            # back -- and 3 and 4 are not positions at all.
            'slot': DEFAULT_SLOTS[len(units)],
            'equip': equip,
        })

    if not units:
        problems.append('a team needs at least one hero')

    clean = {
        'index': 900000 + index,       # well clear of AIIndex 9001..9887
        'name': name,
        'points': max(0, to_int(bot.get('points'), 0)),
        'exp': max(0, to_int(bot.get('exp'), 1000)),
        'profile': max(0, to_int(bot.get('profile'), 1)),
        'commander': max(0, to_int(bot.get('commander'), 1)),
        'commander_level': max(1, to_int(bot.get('commander_level'), 1)),
        'units': units,
        'custom': True,
    }
    return clean, problems


def _custom_bots():
    """Teams built in the dashboard, sanitised on the way out.

    Cleaning here as well as on save is deliberate: a settings.json written by
    an older build, or edited by hand, must not be able to put a hero in a
    slot that does not exist and black-screen the battle scene.
    """
    out = []
    for i, bot in enumerate(SETTINGS.get('arena.bots', []) or []):
        clean, problems = check_bot(bot, i)
        if not clean['units']:
            log.warning('arena: ignoring bot %r -- %s',
                        clean['name'], '; '.join(problems))
            continue
        if problems:
            log.info('arena: repaired bot %r -- %s',
                     clean['name'], '; '.join(problems))
        out.append(clean)
    return out


def bots():
    """Every bot the ladder can offer.  `arena.use_shipped_bots` off leaves
    only what the dashboard defines, which is how you curate a small ladder."""
    out = list(_custom_bots())
    if SETTINGS.get('arena.use_shipped_bots', True):
        out.extend(_shipped_bots())
    return out


def bot_by_id(account_id):
    want = abs(int(account_id))
    for bot in bots():
        if bot['index'] == want:
            return bot
    return None


# --------------------------------------------------------------- a record --
def record(player):
    """The player's arena state, created on first look."""
    rec = player.d.setdefault('arena', {})
    rec.setdefault('season', int(SETTINGS.get('arena.season', 1)))
    rec.setdefault('points', int(SETTINGS.get('arena.starting_points', 0)))
    rec.setdefault('wins', 0)
    rec.setdefault('losses', 0)
    rec.setdefault('defense', [])          # [{slot_type, slot_index, unit_uid}]
    rec.setdefault('matches', [])          # the opponents currently on offer
    rec.setdefault('log', [])              # recent battles, newest first
    rec.setdefault('rewards_taken', 0)     # tiers already claimed
    rec.setdefault('daily', {})            # 'YYYY-MM-DD' -> fights played
    # A season bump in settings resets the ladder, which is what a season is.
    season = int(SETTINGS.get('arena.season', 1))
    if rec['season'] != season:
        rec.update(season=season, points=int(SETTINGS.get('arena.starting_points', 0)),
                   wins=0, losses=0, matches=[], rewards_taken=0)
    return rec


def today():
    return datetime.utcnow().strftime('%Y-%m-%d')


def plays_today(player):
    return int(record(player)['daily'].get(today(), 0))


def plays_left(player):
    cap = int(SETTINGS.get('arena.daily_battles', 0) or 0)
    return -1 if cap <= 0 else max(cap - plays_today(player), 0)


def note_play(player):
    rec = record(player)
    rec['daily'][today()] = plays_today(player) + 1
    # Keep the map from growing forever.
    for day in sorted(rec['daily'])[:-7]:
        rec['daily'].pop(day, None)


def defense_units(player):
    """The units a player defends with, in slot order.  Falls back to their
    story party, then to their strongest heroes -- an empty defence team would
    make the account unmatchable, and a player who never opens the arena screen
    should still be a valid opponent."""
    by_uid = {int(u['uid']): u for u in player.d.get('units', [])}
    rec = record(player)
    for source in (rec.get('defense'), player.d.get('party')):
        picked = [by_uid[int(r['unit_uid'])] for r in (source or [])
                  if int(r.get('unit_uid', 0)) in by_uid]
        if picked:
            return picked[:4]
    units = sorted(player.d.get('units', []),
                   key=lambda u: (u.get('rareness', 0), u.get('level', 1)),
                   reverse=True)
    return units[:4]


def power(player_or_units):
    units = (player_or_units if isinstance(player_or_units, list)
             else defense_units(player_or_units))
    return sum(int(u.get('power', u.get('level', 1) * 100)) for u in units)


def bot_power(bot):
    return sum(int(u['level']) * 100 for u in bot['units'])


# ------------------------------------------------------- the opponent pool --
# Scanning every save to find opponents is fine at this scale (a handful of
# accounts) but it is disk work on a packet path, so it is cached briefly.
# The cache only affects who is *offered*; a fight always re-reads the save.
_POOL_TTL = 20.0
_pool_cache = {'at': 0.0, 'rivals': []}


def rivals(exclude_account_id=None, force=False):
    """Every other real account that can be fought, newest snapshot wins."""
    from .player import Player, ACCOUNTS_DIR
    import os
    import re

    now = time.time()
    if not force and now - _pool_cache['at'] < _POOL_TTL:
        found = _pool_cache['rivals']
    else:
        found = []
        try:
            names = os.listdir(ACCOUNTS_DIR)
        except OSError:
            names = []
        for name in names:
            if not re.fullmatch(r'\d+\.json', name):
                continue
            other = Player.load(int(name[:-5]))
            if other is None or not other.d.get('units'):
                continue
            rec = other.d.get('arena') or {}
            found.append({
                'account_id': other.account_id,
                'name': other.d.get('nickname') or ('Player%d' % other.account_id),
                'points': int(rec.get('points', 0)),
                'exp': int(other.d.get('exp', 0)),
                'profile': int(other.d.get('represent_profile', 0)),
                'skin': int(other.d.get('skin_id', 0)),
                'units': defense_units(other),
            })
        _pool_cache.update(at=now, rivals=found)
    if exclude_account_id is None:
        return list(found)
    return [r for r in found if int(r['account_id']) != int(exclude_account_id)]


def invalidate_pool():
    """Called when a save changes underneath us -- a defence team edit, or the
    dashboard rewriting an account."""
    _pool_cache['at'] = 0.0


def _near(candidates, points, span):
    within = [c for c in candidates if abs(int(c['points']) - int(points)) <= span]
    return within or candidates


def roll_matches(player, count=MATCH_SLOTS, rng=random):
    """Pick a fresh set of opponents for the arena screen.

    The tier decides both how far matchmaking reaches (`MatchRange`) and how
    often it should offer a bot rather than a person (`AiMatchRate`).  With one
    account on the server there is nobody else to offer, so it is bots all the
    way down without needing a special case.
    """
    rec = record(player)
    row = tier_for(rec['points'])
    span = to_int(row.get('MatchRange'), 1000)
    try:
        ai_rate = float(row.get('AiMatchRate') or 1.0)
    except (TypeError, ValueError):
        ai_rate = 1.0
    override = SETTINGS.get('arena.ai_match_rate', -1)
    if override is not None and float(override) >= 0:
        ai_rate = float(override)

    people = _near(rivals(player.account_id), rec['points'], span)
    machines = _near(bots(), rec['points'], span)

    out, used = [], set()
    for index in range(count):
        want_bot = (not people) or rng.random() < ai_rate
        pool = machines if want_bot else people
        if not pool:
            pool = people or machines
        if not pool:
            break
        pick = None
        for _ in range(12):                # a few tries to avoid duplicates
            cand = rng.choice(pool)
            key = ('bot' if 'index' in cand else 'user',
                   cand.get('index', cand.get('account_id')))
            if key not in used:
                used.add(key)
                pick = cand
                break
        if pick is None:
            continue
        out.append(_as_match(pick, index))
    rec['matches'] = out
    return out


def _as_match(entry, index):
    """One offered opponent, flattened so it survives a save/load round trip."""
    if 'index' in entry:                   # a bot
        return {
            'slot': index,
            'account_id': bot_account_id(entry['index']),
            'bot_index': entry['index'],
            'name': entry['name'],
            'points': entry['points'],
            'exp': entry['exp'],
            'profile': entry['profile'],
            'skin': 0,
            'power': bot_power(entry),
            'beaten': 0,
        }
    return {
        'slot': index,
        'account_id': entry['account_id'],
        'bot_index': None,
        'name': entry['name'],
        'points': entry['points'],
        'exp': entry['exp'],
        'profile': entry['profile'],
        'skin': entry.get('skin', 0),
        'power': power(entry['units']),
        'beaten': 0,
    }


def find_match(player, account_id, index=None):
    """The offered opponent the client is referring to."""
    for m in record(player)['matches']:
        if int(m['account_id']) == int(account_id):
            return m
        if index is not None and int(m['slot']) == int(index):
            return m
    return None


# ContentsType, from the client's own enum @dump.cs:632968.  The two that
# matter here are the party types the arena uses:
#   5 = ArenaDefense -- the team that defends, and the one the client looks up
#                       to build the enemy side of the battle
#   6 = ArenaAttack  -- the team you attack with
CONTENTS_ARENA_DEFENSE = 5
CONTENTS_ARENA_ATTACK = 6

def opponent_lineup(match):
    """[(unit dict, slot index)] for an offered opponent.

    The slot matters as much as the unit: the client builds the enemy side by
    looking up the ArenaDefense party and placing each UnitUID at its
    SlotIndex.  A line-up with the wrong slots -- or the wrong SlotType -- puts
    nothing on the field, and the fight is won the instant it starts.
    """
    if match.get('bot_index') is not None:
        bot = bot_by_id(match['account_id'])
        if bot is None:
            return []
        out = []
        for i, u in enumerate(bot['units'][:MAX_TEAM]):
            # Last line of defence: a slot that is not an EUnitPosition takes
            # the battle scene down with a NullReferenceException inside
            # PlayBaseScene.UnitLoading, which shows up as a black screen and
            # says nothing.  Anything unrecognised is placed by position.
            slot = int(u.get('slot') or 0)
            if slot not in DEFAULT_SLOTS:
                slot = DEFAULT_SLOTS[i]
            out.append(({
                'uid': 900000 + i, 'id': u['id'], 'level': u['level'],
                'tier': u['tier'], 'grade': u['grade'], 'rareness': u['rareness'],
                'power': u['level'] * 100,
                'equip': {str(n + 1): e for n, e in enumerate(u['equip']) if e > 0},
            }, slot))
        return out

    from .player import Player
    other = Player.load(int(match['account_id']))
    if other is None:
        return []
    units = defense_units(other)[:MAX_TEAM]
    # Prefer the slots the defender actually chose -- but only if they are
    # real positions, for the same reason as the bot branch above.
    by_uid = {int(r.get('unit_uid', 0)): int(r.get('slot_index', 0))
              for r in (record(other).get('defense') or [])}
    out = []
    for i, u in enumerate(units):
        slot = by_uid.get(int(u['uid']), 0)
        if slot not in DEFAULT_SLOTS:
            slot = DEFAULT_SLOTS[i]
        out.append((u, slot))
    return out


def opponent_units(match):
    """Just the units, for callers that do not care where they stand."""
    return [u for u, _slot in opponent_lineup(match)]


# ------------------------------------------------------------------- DTOs --
def _empty(name):
    """A zeroed nested struct.  Never send a null one -- it throws inside the
    client's own marshaller, not ours, so the failure is invisible here."""
    return TYPES[name]()


def arena_info(account_id, nickname, points, wins, losses, exp=0, profile=0,
               skin=0, rank=0, reward_count=0):
    """NGArenaInfo -- one competitor's ladder standing."""
    now = datetime.utcnow()
    return TYPES['NGArenaInfo'](
        iAccountID=int(account_id), iSeasonID=int(SETTINGS.get('arena.season', 1)),
        iMatchPoint=int(points), strNickname=str(nickname),
        iExp=int(exp), iWinCount=int(wins), iLoseCount=int(losses),
        iGrade=tier_number(points), iRewardCount=int(reward_count),
        tmLastRecvTime=now, tmUpdateTime=now, iRank=int(rank),
        RepresentProfile=int(profile), GroupID=match_group(points),
        MatchUpdateEnable=1, Winning=int(wins), Losing=int(losses),
        TestExamWin=0, TestExamLose=0, isTestExam=False, vecAwakenStat=[],
        TestExamWinning=0, TestExamLosing=0, SkinID=int(skin),
        ngCommandCenterInfo=_empty('NGCommandCenterInfo'),
        ngCommandersInfo=_empty('NGCommandersInfo'),
        frameInfo=_empty('NGFrameInfo'), vecUnitCollection=[])


def player_arena_info(player, rank=0):
    rec = record(player)
    return arena_info(player.account_id, player.nickname, rec['points'],
                      rec['wins'], rec['losses'], player.d.get('exp', 0),
                      player.d.get('represent_profile', 0),
                      player.d.get('skin_id', 0), rank, rec['rewards_taken'])


def match_user(match):
    """NGArenaMatchUser -- one opponent card on the arena screen.

    vceUnitPartyInfo is what the client turns into the enemy side of the
    battle, so it has to be the *defence* party (ContentsType 5) at the slots
    the game actually uses -- 1, 2, 10, 11.  With SlotType 0 and slots 0..3 the
    lookup finds nobody and you win before the fight starts.
    """
    lineup = opponent_lineup(match)
    return TYPES['NGArenaMatchUser'](
        iMatchIndex=int(match['slot']),
        ngArenaInfo=arena_info(match['account_id'], match['name'],
                               match['points'], 0, 0, match['exp'],
                               match['profile'], match.get('skin', 0)),
        iMatchResult=int(match.get('beaten', 0)),
        vecUnitInfo=[state.unit_info(u) for u, _ in lineup],
        tmRecvTime=datetime.utcnow(),
        vceUnitPartyInfo=[TYPES['NGPartyInfo'](
            SlotType=CONTENTS_ARENA_DEFENSE, SlotIndex=slot,
            UnitUID=int(u['uid']), SkillOnOff=1)
            for u, slot in lineup],
        UserMatchGroup=match_group(match['points']))


def ranker(entry, rank):
    """NGRanker -- one row of the leaderboard."""
    units = entry['units'] if isinstance(entry.get('units'), list) else []
    if entry.get('bot'):
        units = [{'uid': 900000 + i, 'id': u['id'], 'level': u['level'],
                  'tier': u['tier'], 'grade': u['grade'],
                  'rareness': u['rareness'], 'power': u['level'] * 100}
                 for i, u in enumerate(units)]
    return TYPES['NGRanker'](
        AccountID=int(entry['account_id']), Rank=int(rank),
        FightingPower=int(entry.get('power', 0)),
        Nickname=str(entry['name']),
        vecPartyUnitInfo=[state.unit_info(u) for u in units],
        iMatchPoint=int(entry['points']), iGarde=tier_number(entry['points']),
        iExp=int(entry.get('exp', 0)),
        RepresentProfile=int(entry.get('profile', 0)),
        SkinID=int(entry.get('skin', 0)), frameInfo=_empty('NGFrameInfo'))


def leaderboard(player, limit=100):
    """Everyone on the ladder, best first.  Bots included -- on a one-player
    server a leaderboard of one is not much of a leaderboard."""
    rec = record(player)
    rows = [{'account_id': player.account_id, 'name': player.nickname,
             'points': rec['points'], 'exp': player.d.get('exp', 0),
             'profile': player.d.get('represent_profile', 0),
             'skin': player.d.get('skin_id', 0),
             'power': power(player), 'units': defense_units(player)}]
    for r in rivals(player.account_id):
        rows.append({'account_id': r['account_id'], 'name': r['name'],
                     'points': r['points'], 'exp': r['exp'],
                     'profile': r['profile'], 'skin': r.get('skin', 0),
                     'power': power(r['units']), 'units': r['units']})
    if SETTINGS.get('arena.bots_on_leaderboard', True):
        for bot in bots():
            rows.append({'account_id': bot_account_id(bot['index']),
                         'name': bot['name'], 'points': bot['points'],
                         'exp': bot['exp'], 'profile': bot['profile'],
                         'power': bot_power(bot), 'units': bot['units'],
                         'bot': True})
    rows.sort(key=lambda r: (-int(r['points']), -int(r['power'])))
    return rows[:limit]


def my_rank(player):
    board = leaderboard(player, limit=100000)
    for i, row in enumerate(board, 1):
        if int(row['account_id']) == int(player.account_id) and not row.get('bot'):
            return i
    return len(board)


def battle_log(player, limit=20):
    return [TYPES['NGArenaBattleLog'](
        iLogIndex=int(e['index']), iSeasonID=int(e.get('season', 1)),
        iWinLose=int(e['win']),
        iAttackUserAccountID=int(e['attacker']),
        strAttackUserNickName=str(e['attacker_name']),
        iAttackUserExp=int(e.get('attacker_exp', 0)),
        iAttackUserBeforeMatchPoint=int(e['before']),
        iAttackUserAfterMatchPoint=int(e['after']),
        iAttackUserTotalPower=int(e.get('attacker_power', 0)),
        iAttackUserUnitID=int(e.get('attacker_unit', 0)),
        iAttackUserTier=tier_number(e['after']),
        iDefenseUserAccountID=int(e['defender']),
        strDefenseUserNickName=str(e['defender_name']),
        iDefenseUserExp=int(e.get('defender_exp', 0)),
        iDefenseUserBeforeMatchPoint=int(e.get('defender_points', 0)),
        iDefenseUserAfterMatchPoint=int(e.get('defender_points', 0)),
        iDefenseUserTotalPower=int(e.get('defender_power', 0)),
        iDefenseUserUnitID=int(e.get('defender_unit', 0)),
        iDefenseUserTier=tier_number(e.get('defender_points', 0)),
        tmRegDate=state._dt(e.get('at')),
        strPlayRecordInfo=str(e.get('record', '')),
        AttackUserSkinID=0, DefenseUserSkinID=0,
        AttackFrameInfo=_empty('NGFrameInfo'),
        DefenseFrameInfo=_empty('NGFrameInfo'))
        for e in record(player)['log'][:limit]]


def resolve(player, match, won, record_info=''):
    """Apply the outcome of one fight.  Returns (result_dto, rewards)."""
    rec = record(player)
    before = int(rec['points'])
    delta = point_delta(before, won, match['points'],
                        match.get('bot_index') is not None)
    after = max(before + delta, 0)
    rec['points'] = after
    rec['wins' if won else 'losses'] += 1
    match['beaten'] = 1 if won else 2
    note_play(player)

    units = defense_units(player)
    rec['log'].insert(0, {
        'index': int(time.time() * 1000) % 2147483647,
        'season': rec['season'], 'win': 1 if won else 0,
        'attacker': player.account_id, 'attacker_name': player.nickname,
        'attacker_exp': player.d.get('exp', 0),
        'attacker_power': power(units),
        'attacker_unit': units[0]['id'] if units else 0,
        'before': before, 'after': after,
        'defender': match['account_id'], 'defender_name': match['name'],
        'defender_exp': match['exp'], 'defender_points': match['points'],
        'defender_power': match.get('power', 0),
        'at': datetime.utcnow().isoformat(timespec='seconds'),
        'record': record_info or '',
    })
    del rec['log'][40:]

    rewards = tier_rewards(before) if won else []
    result = TYPES['NGArenaResult'](
        iWinLose=1 if won else 0, iBeforeMatchPoint=before,
        iAfterMatchPoint=after, iBeforeGrade=tier_number(before),
        iAfterGrade=tier_number(after))
    return result, rewards
