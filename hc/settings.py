"""Live, editable server settings.

`config.py` holds the things that have to be right for the client to talk to us
at all -- ports, the string-table id it feeds to int.Parse -- and those are
env-vars because getting one wrong breaks login.  This is the other kind: knobs
a server owner legitimately wants to turn, at runtime, without a restart.  It
backs the web dashboard.

Everything lives in one JSON file (``server/settings.json``, gitignored) and
everything has a default here, so a missing or half-written file still boots.
Values are addressed with dotted paths::

    SETTINGS.get('gacha.duplicate.0')
    SETTINGS.set('rewards.multiplier', 2.5)

Game code should read through `SETTINGS` at the point of use rather than
caching at import, or a change in the dashboard will not take effect until the
next restart -- which is the whole point of this module.
"""
import copy
import json
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.environ.get('HC_SETTINGS') or os.path.join(ROOT, 'settings.json')

# Every knob, with the value that reproduces current behaviour.  A key absent
# from settings.json falls back to here, so this doubles as the documentation
# of what can be changed.
DEFAULTS = {
    'gacha': {
        # Per-banner rarity weights, overriding unitGachaID.Rate.
        #   {"2": {"0": 845, "1": 130, "2": 25}}   (rareness -> weight)
        # Weights are relative; they do not have to add up to anything.
        'rates': {},
        # Per-banner cost, overriding GachaList.
        #   {"1": {"type1": 33, "type2": -1, "single": 5, "ten": 45}}
        'cost': {},
        # What a duplicate hero converts to, by the duplicate's rareness.
        #   {"0": [[45, 1, -1, 1]]}       -> [type1, type2, type3, amount]
        #   {"2": [[16, "unit", -1, 40]]} -> "unit" = the hero just summoned
        # Empty means the built-in behaviour: A and S give a generic Memory
        # Stone from DecompPieceReturn, SS and above give 40 of that hero's own
        # Memory (ResourceType 16 keyed by unit id) -- one star-up's worth.
        'duplicate': {},
        # false -> a duplicate mints a second copy of the hero instead of
        # converting.  Off by default because that is not how the game works.
        'duplicates_as_units': False,
    },
    'rewards': {
        # Multiplies every stage drop.  1.0 is retail.
        'multiplier': 1.0,
        # Multiplies first-clear drops on top of the above.
        'first_clear_multiplier': 1.0,
        # Replace a stage's drops outright:
        #   {"1": [[4, -1, -1, 1000]]}   dungeonID -> [[t1, t2, t3, amount]]
        'stage_overrides': {},
        # Rank EXP granted per stage clear. Retail fed this from City Search
        # claims, which this server does not implement; the stage's stamina
        # cost stands in for it.  Set to 0 for a from-scratch progression run.
        'rank_exp_per_clear': -1,      # -1 = keep the stamina-cost behaviour
    },
    'account': {
        # Extra or replacement starting resources for *new* accounts:
        #   {"1": 5000000}          plain type -> amount
        #   {"45:1": 5000}          "type1:type2" for typed sub-currencies
        'starting_resources': {},
        # Give every new account one copy of every playable hero.
        'grant_all_heroes': True,
        # Start at the rank-30 EXP threshold so hero levelling is not blocked
        # out of the gate.  0 starts at rank 1.
        'starting_exp': -1,            # -1 = keep the built-in default
    },
    'arena': {
        # Bump this to wipe the ladder and start a new season.
        'season': 1,
        'starting_points': 0,
        # 0 = unlimited fights per day.
        'daily_battles': 0,
        # Hand-built bot teams, newest first in the dashboard. Each is
        #   {"name": ..., "points": 1200, "exp": 5000, "profile": 1,
        #    "units": [{"id": 41, "level": 60, "tier": 5, "grade": 3,
        #               "rareness": 2}, ...]}
        # These are offered alongside (or instead of) the shipped ones.
        'bots': [],
        # The client shipped 887 AI teams in ArenaAIList/ArenaAITeamInfo, so a
        # one-player server has a full ladder without configuring anything.
        # Turn this off to run a ladder of only your own teams.
        'use_shipped_bots': True,
        # Bots appear on the leaderboard too -- a board of one is not a board.
        'bots_on_leaderboard': True,
        # -1 keeps ArenaTierInfo.AiMatchRate, which is how often matchmaking
        # should offer a bot rather than a real player at your tier. Force it
        # to 0 to only ever match people, 1 to only ever match bots.
        'ai_match_rate': -1,
        'leaderboard_size': 100,
    },
    'equipment': {
        # Equipping moves a copy out of the wallet, so one sword cannot be worn
        # by the whole roster.  false makes gear free.
        'consume_on_equip': True,
    },
    'dashboard': {
        # The dashboard binds loopback by default.  It has no authentication
        # and can rewrite any save, so only widen this on a network you trust.
        'bind': '127.0.0.1',
        'port': 8099,
        # Require this token as ?token=... when the bind address is not
        # loopback.  Empty means no token, which is refused for non-loopback.
        'token': '',
    },
}

_LOCK = threading.Lock()


def _merge(base, over):
    """Deep-merge `over` onto a copy of `base`."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings(object):
    def __init__(self, path=PATH):
        self.path = path
        self._raw = {}
        self.load()

    # -- persistence -------------------------------------------------------
    def load(self):
        """(Re)read settings.json.  A broken file is ignored, not fatal --
        the server must still come up so the dashboard can fix it."""
        with _LOCK:
            try:
                with open(self.path, encoding='utf-8') as fh:
                    raw = json.load(fh)
                self._raw = raw if isinstance(raw, dict) else {}
            except (IOError, OSError, ValueError):
                self._raw = {}
        return self

    def save(self):
        with _LOCK:
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(self._raw, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)      # atomic, so a crash cannot truncate

    # -- access ------------------------------------------------------------
    @property
    def all(self):
        """Defaults with the file's overrides merged in."""
        return _merge(DEFAULTS, self._raw)

    @property
    def overrides(self):
        """Only what has actually been changed from the defaults."""
        return copy.deepcopy(self._raw)

    def get(self, path, default=None):
        node = self.all
        for part in path.split('.'):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path, value, save=True):
        parts = path.split('.')
        node = self._raw
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value
        if save:
            self.save()
        return value

    def reset(self, path=None, save=True):
        """Drop an override so the default applies again; no path resets all."""
        if path is None:
            self._raw = {}
        else:
            parts = path.split('.')
            node = self._raw
            for part in parts[:-1]:
                node = node.get(part)
                if not isinstance(node, dict):
                    return
            node.pop(parts[-1], None)
        if save:
            self.save()

    def update(self, tree, save=True):
        """Merge a whole subtree of overrides in one go."""
        self._raw = _merge(self._raw, tree)
        if save:
            self.save()


SETTINGS = Settings()
