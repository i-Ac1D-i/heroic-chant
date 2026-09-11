"""Persistent player state.

One JSON file per account under ``server/accounts/``.  Kept deliberately
plain so a save can be hand-edited between sessions.
"""
import json
import os
import threading
from datetime import datetime

from .enums import ResourceType
from ..data.tables import TABLES, to_int
from ..settings import SETTINGS

ACCOUNTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'accounts')

_LOCK = threading.Lock()

# What a brand new account starts with.
STARTING_RESOURCES = {
    ResourceType.Gold: 5_000_000,
    ResourceType.Cash: 50_000,               # gems
    ResourceType.TotalDungeonTicket: 999,    # story stamina
    ResourceType.HardDungeonTicket: 99,
    ResourceType.CharEXP: 1_000_000,
    ResourceType.LvUpMet: 100_000,
    ResourceType.TierUpMet: 100_000,
    ResourceType.FriendPoint: 10_000,
    ResourceType.HeroCoin: 10_000,
    ResourceType.ArenaTicket: 99,
    ResourceType.HeroDungeonTicket: 99,
    ResourceType.Unit_Inven_Max: 900,
    ResourceType.Res_Inven_Max: 900,
    ResourceType.Artifact_Inven_Max: 900,
}

# Star-up and Awakening are paid for in typed sub-currencies, so the flat
# {type: amount} table above cannot express them.  Keys are (Type1, Type2):
#   13/x  elemental Shards (1 Fire, 2 Ice, 3 Nature, 4 Chaos, ...)
#   45/x  Memory Stones ([A], [S], [SS])
#   130/x Miraculous / Necromantic Stones -- awakening node costs
#   12    Evolution material, 144 Refined Essence, 115 Essence of Abyss
STARTING_TYPED_RESOURCES = {}
for _t2 in range(1, 9):
    STARTING_TYPED_RESOURCES[(13, _t2)] = 20_000        # every element's Shard
for _t2 in (1, 2, 3):
    STARTING_TYPED_RESOURCES[(45, _t2)] = 5_000         # [A] / [S] / [SS] stones
for _t2 in range(1, 21):
    STARTING_TYPED_RESOURCES[(130, _t2)] = 2_000        # awakening stones
STARTING_TYPED_RESOURCES[(12, -1)] = 10_000             # Evolution material
STARTING_TYPED_RESOURCES[(144, -1)] = 1_000             # Refined Essence
STARTING_TYPED_RESOURCES[(115, -1)] = 5_000             # Essence of Abyss
STARTING_TYPED_RESOURCES[(33, -1)] = 500                # summon tickets
# ArtifactMaterial (127) is the Forge's "Craft KIT" -- string 11593 calls it
# the Relic Craft KIT and says relics are crafted at the Forge.  A level-5
# craft costs 3,000, so this is a handful of good crafts to start with.
STARTING_TYPED_RESOURCES[(127, -1)] = 20_000


def _starting_gear():
    """A spread of low-grade gear so every slot has something to put in it.

    Gear is stackable and lives in the wallet as ResourceType.Item (5) keyed by
    itemID, so this is just more resource rows.  Kept to a sample rather than
    all 2 526 items -- the login payload has to stay under MAX_PACKET_SIZE.
    """
    from ..data.tables import TABLES, to_int
    out = {}
    per_slot = {}
    for row in TABLES.sql('itemList'):
        slot = to_int(row.get('itemType'), -1)
        if slot not in (1, 2, 3, 4) or to_int(row.get('itemGrade'), 9) > 2:
            continue
        if per_slot.get(slot, 0) >= 25:
            continue
        per_slot[slot] = per_slot.get(slot, 0) + 1
        out[(5, to_int(row['itemID']))] = 10
    return out


def _starting_memories():
    """A stock of every hero's own Memory, so star-up can get past 3 stars.

    `GradeUpMaterial` charges ResourceType.UnitPieces (16) keyed by the hero's
    own unit id from grade 2 onwards -- 80, then 160, 240 and 320, so 800 takes
    one hero from 3 stars to 6.  Nothing else in this server pays out type 16:
    the only source the real game had here is an SS-or-better duplicate summon,
    which means a hero you pulled once could never be starred up at all.  That
    is what "hero X has no memories" is.

    Seeded per hero rather than as one generic currency because the client
    charges per hero and shows "<name>'s Memory" for each.  Set
    ``account.starting_memories`` to 0 in the dashboard to turn this off and
    make duplicates matter again.
    """
    from ..data.tables import TABLES, to_int
    each = SETTINGS.get('account.starting_memories', 1000)
    if int(each) <= 0:
        return {}
    out = {}
    for row in TABLES.sql('UnitList'):
        if to_int(row.get('IsPlayerHero')) != 1:
            continue
        # UnitPieceID is the shard's own id.  It equals UnitID for all 138
        # player heroes in this build, but the client charges by whichever the
        # table says, so follow the table.
        piece = to_int(row.get('UnitPieceID'), -1)
        if piece < 0:
            piece = to_int(row.get('UnitID'), -1)
        if piece >= 0:
            out[(ResourceType.UnitPieces, piece)] = int(each)
    return out


STARTING_TYPED_RESOURCES.update(_starting_gear())
STARTING_TYPED_RESOURCES.update(_starting_memories())


def _rk(t1, t2=-1, t3=-1):
    return '%d:%d:%d' % (t1, t2, t3)


def _starting_wallet():
    """The tables above, with whatever the dashboard has overridden on top.

    Override keys are ``"<type1>"`` or ``"<type1>:<type2>"``; a value of 0
    removes the entry, so a server owner can take the training wheels off as
    well as pile more on.
    """
    wallet = dict(
        [(_rk(t), v) for t, v in STARTING_RESOURCES.items()]
        + [(_rk(t1, t2), v) for (t1, t2), v in STARTING_TYPED_RESOURCES.items()])
    for key, value in (SETTINGS.get('account.starting_resources', {}) or {}).items():
        parts = [int(x) for x in str(key).split(':')]
        parts += [-1] * (3 - len(parts))
        k = _rk(*parts[:3])
        if int(value) <= 0:
            wallet.pop(k, None)
        else:
            wallet[k] = int(value)
    return wallet


class Player(object):
    def __init__(self, data):
        self.d = data
        # Resource keys touched since the last sync to the client.  Not
        # persisted -- it only tracks what this session still owes the client.
        self._dirty = set()
        self._dirty_collections = set()

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def path(cls, account_id):
        return os.path.join(ACCOUNTS_DIR, '%d.json' % account_id)

    @classmethod
    def load(cls, account_id):
        p = cls.path(account_id)
        if os.path.exists(p):
            with open(p, encoding='utf-8') as fh:
                return cls(json.load(fh))
        return None

    @classmethod
    def create(cls, account_id, device_id, nickname=None):
        now = datetime.utcnow().isoformat(timespec='seconds')
        pl = cls({
            'account_id': account_id,
            'device_id': device_id,
            'nickname': nickname or ('Player%d' % account_id),
            # Account rank caps how far heroes can be levelled, and rank EXP
            # normally trickles in from City Search claims that this server does
            # not implement yet.  157_800 is the UserRankEXP threshold for rank
            # 30, which lifts that cap and matches the generous starter package
            # above.  Set to 0 for a from-scratch progression run.
            'exp': (157_800 if SETTINGS.get('account.starting_exp', -1) < 0
                    else int(SETTINGS.get('account.starting_exp'))),
            'represent_profile': 5,
            'skin_id': 0,
            'reg_date': now,
            'last_login': now,
            'next_uid': 1,
            'resources': _starting_wallet(),
            'units': [],
            'party': [],
            'cleared': {},          # dungeon_id -> star flag bitmask
            'collections': {},      # "t1:t2:t3" -> counter (see CollectionType)
            'tutorials': list(range(1, 200)),   # skip the tutorial gate
            'commanders': [{'id': 1, 'level': 1, 'tier': 1}],
            'artifacts': [],     # Artifacts: per-instance, not wallet rows
            'scenecards': [],    # Relics (Scene Cards): likewise
            'forge': {},         # craft slots, keyed by slot index
            'awaken_stats': [],  # opened awakenRoleStatIDs
            'missions_done': [],
            'afk_claimed': now,     # City Search: last time idle rewards were taken
        })
        if SETTINGS.get('account.grant_all_heroes', True):
            pl.grant_starter_units()
        pl.grant_starter_relics()
        pl.save()
        return pl

    def save(self):
        with _LOCK:
            os.makedirs(ACCOUNTS_DIR, exist_ok=True)
            tmp = self.path(self.account_id) + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(self.d, fh, indent=1)
            os.replace(tmp, self.path(self.account_id))

    # -- scalars -----------------------------------------------------------
    account_id = property(lambda s: s.d['account_id'])
    nickname = property(lambda s: s.d['nickname'])

    # -- resources ---------------------------------------------------------
    def get_resource(self, t1, t2=-1, t3=-1):
        return int(self.d['resources'].get(_rk(t1, t2, t3), 0))

    def add_resource(self, t1, value, t2=-1, t3=-1):
        k = _rk(t1, t2, t3)
        self.d['resources'][k] = int(self.d['resources'].get(k, 0)) + int(value)
        self._dirty.add((t1, t2, t3))
        return self.d['resources'][k]

    def take_dirty(self):
        """Resource keys changed since the last sync; clears the set."""
        d, self._dirty = self._dirty, set()
        return d

    # ResourceType.TotalCash (21) is not a wallet of its own: it is the sum of
    # EventCash (20) and Cash (19), and prices are quoted in it.  Spending it
    # draws event currency first, exactly as the shop UI describes.
    TOTAL_CASH, EVENT_CASH, CASH = 21, 20, 19

    def spend_resource(self, t1, value, t2=-1, t3=-1):
        """Returns False (and changes nothing) when the player cannot afford it."""
        if value <= 0:
            return True
        if t1 == self.TOTAL_CASH:
            return self._spend_total_cash(value)
        if self.get_resource(t1, t2, t3) < value:
            return False
        self.add_resource(t1, -value, t2, t3)
        return True

    def total_cash(self):
        return self.get_resource(self.EVENT_CASH) + self.get_resource(self.CASH)

    def _spend_total_cash(self, value):
        if self.total_cash() < value:
            return False
        from_event = min(self.get_resource(self.EVENT_CASH), value)
        if from_event:
            self.add_resource(self.EVENT_CASH, -from_event)
        rest = value - from_event
        if rest:
            self.add_resource(self.CASH, -rest)
        return True

    def resource_items(self):
        for k, v in self.d['resources'].items():
            t1, t2, t3 = (int(x) for x in k.split(':'))
            yield t1, t2, t3, int(v)

    # -- units -------------------------------------------------------------
    def new_uid(self):
        uid = self.d['next_uid']
        self.d['next_uid'] += 1
        return uid

    def add_unit(self, unit_id, level=1, tier=1, grade=1, rareness=None):
        # NGUnitInfo.currentRareness drives the A / S / SS letter on the card.
        # It starts at the hero's base rarity from UnitList.Rareness (0 = A,
        # 1 = S, 2 = SS, 3 = the top tier) and rises through rarenessInfo's
        # 0->1->2->3 upgrade path.  Defaulting it to 0 makes the entire roster
        # read as A, which is what happened before.
        if rareness is None:
            row = TABLES.unit(unit_id) or {}
            rareness = to_int(row.get('Rareness'), 0)
        u = {'uid': self.new_uid(), 'id': int(unit_id), 'level': level,
             'tier': tier, 'grade': grade, 'rareness': max(int(rareness), 0),
             'reg_date': datetime.utcnow().isoformat(timespec='seconds')}
        self.d['units'].append(u)
        return u

    # -- parties -----------------------------------------------------------
    # Every party the player has -- story, arena attack, guild war and the
    # rest -- lives in ONE flat list, told apart by NGPartyInfo.SlotType.
    # That is not a shortcut: it is how the client sends them back in
    # NGLoginAckLargeData.vecPartyInfo, one list with mixed SlotTypes.
    # Writing `d['party'] = ...` wholesale therefore silently deletes every
    # other party, which is what stopped the arena attack team sticking.
    def parties(self):
        return self.d.setdefault('party', [])

    def party(self, slot_type):
        return [r for r in self.parties()
                if int(r.get('slot_type', 0)) == int(slot_type)]

    def set_party(self, slot_type, rows):
        """Replace one party, leaving the others alone."""
        slot_type = int(slot_type)
        kept = [r for r in self.parties()
                if int(r.get('slot_type', 0)) != slot_type]
        fresh = [dict(r, slot_type=slot_type) for r in rows]
        self.d['party'] = kept + fresh
        return fresh

    def find_unit(self, uid):
        return next((u for u in self.d['units'] if u['uid'] == int(uid)), None)

    # -- relics (artifacts) ------------------------------------------------
    #
    # Relics are per-instance objects with their own UID, not stackable wallet
    # rows, so they get their own list.  `artifacts` is created lazily because
    # saves written before relics existed will not have the key.
    def artifacts(self):
        return self.d.setdefault('artifacts', [])

    def find_artifact(self, uid):
        return next((a for a in self.artifacts() if a['uid'] == int(uid)), None)

    def add_artifact(self, artifact_id):
        from . import artifacts as art
        a = art.make(self.new_uid(), artifact_id)
        self.artifacts().append(a)
        return a

    def remove_artifact(self, uid):
        a = self.find_artifact(uid)
        if a is not None:
            self.artifacts().remove(a)
        return a

    def artifact_in_slot(self, unit_uid, slot):
        """Whatever relic is currently worn by `unit_uid` in `slot`."""
        from . import artifacts as art
        for a in self.artifacts():
            if int(a.get('equip', 0) or 0) == int(unit_uid)                     and art.slot_of(a['id']) == int(slot):
                return a
        return None

    # -- relics (Scene Cards) ----------------------------------------------
    #
    # "Relic" is what the English UI calls a Scene Card.  These are NOT the
    # artifacts above -- different resource type, different equip slots,
    # different forge.  See hc/game/scenecards.py for the evidence.
    def scenecards(self):
        return self.d.setdefault('scenecards', [])

    def find_scenecard(self, uid):
        return next((r for r in self.scenecards() if r['uid'] == int(uid)), None)

    def add_scenecard(self, card_id):
        from . import scenecards as sc
        r = sc.make(self.new_uid(), card_id)
        self.scenecards().append(r)
        return r

    def remove_scenecard(self, uid):
        r = self.find_scenecard(uid)
        if r is not None:
            self.scenecards().remove(r)
        return r

    def scenecard_in_slot(self, unit_uid, slot):
        for r in self.scenecards():
            if int(r.get('equip', 0) or 0) == int(unit_uid)                     and int(r.get('slot', 0) or 0) == int(slot):
                return r
        return None

    # -- the forge ---------------------------------------------------------
    def forge(self):
        """{"<slot>": {"open": bool, "card": id, "grade": n, "end": iso}}"""
        return self.d.setdefault('forge', {})

    def forge_slot(self, slot_index):
        return self.forge().setdefault(str(int(slot_index)), {})

    def forge_slot_open(self, slot_index):
        from . import scenecards as sc
        if int(slot_index) == sc.free_slot():
            return True
        return bool(self.forge_slot(slot_index).get('open'))

    # -- awakening passive mastery ----------------------------------------
    def awaken_stats(self):
        """Opened awakenRoleStatIDs.  Replayed in NGLogInAck03.vecAwakenStat."""
        return self.d.setdefault('awaken_stats', [])

    def open_awaken_stat(self, stat_id):
        stats = self.awaken_stats()
        if int(stat_id) not in stats:
            stats.append(int(stat_id))
            return True
        return False

    def grant_starter_relics(self):
        from . import artifacts as art
        for artifact_id in art.starter_set():
            self.add_artifact(artifact_id)

    def grant_starter_units(self):
        """Give one copy of every playable hero, so the whole roster is usable."""
        for row in TABLES.units():
            if to_int(row.get('IsPlayerHero'), 0) != 1:
                continue
            self.add_unit(to_int(row['UnitID']), level=1, tier=1, grade=1)

    # -- collection counters -----------------------------------------------
    # The client answers "is this stage cleared?" with
    # NMUserInfo.CheckClearDungeon @0x13A1024, which is literally
    # GetCollectionValue(Type3Key(DungeonClearCount, dungeonID, 0)) > 0.
    # Progression therefore lives in these counters, not in a clear list --
    # without them the client re-locks every stage on the next login.
    # Type3int64 initialises all three components to -1 and the two-argument
    # Type3int64.GetKey(a, b) @0x1CF3FEC sets only Type1 and Type2, leaving
    # Type3 at -1.  CheckClearDungeon therefore looks up (0, dungeonID, -1):
    # storing these with Type3 = 0 makes every lookup miss silently.
    def get_collection(self, t1, t2=-1, t3=-1):
        return int(self.d.setdefault('collections', {}).get(_rk(t1, t2, t3), 0))

    def add_collection(self, t1, value=1, t2=-1, t3=-1):
        k = _rk(t1, t2, t3)
        cols = self.d.setdefault('collections', {})
        cols[k] = int(cols.get(k, 0)) + int(value)
        self._dirty_collections.add((t1, t2, t3))
        return cols[k]

    def set_collection(self, t1, value, t2=-1, t3=-1):
        self.d.setdefault('collections', {})[_rk(t1, t2, t3)] = int(value)
        self._dirty_collections.add((t1, t2, t3))

    def collection_items(self):
        for k, v in self.d.setdefault('collections', {}).items():
            t1, t2, t3 = (int(x) for x in k.split(':'))
            yield t1, t2, t3, int(v)

    def take_dirty_collections(self):
        d, self._dirty_collections = self._dirty_collections, set()
        return d

    # -- gacha -------------------------------------------------------------
    def summon_count(self, gacha_id):
        rec = self.d.setdefault('summons', {}).get(str(gacha_id))
        if not rec:
            return 0, datetime(2000, 1, 1)
        try:
            return int(rec['count']), datetime.fromisoformat(rec['last'])
        except (KeyError, TypeError, ValueError):
            return 0, datetime(2000, 1, 1)

    def bump_summon_count(self, gacha_id, n=1):
        rec = self.d.setdefault('summons', {}).setdefault(
            str(gacha_id), {'count': 0, 'last': datetime(2000, 1, 1).isoformat()})
        rec['count'] = int(rec.get('count', 0)) + int(n)
        rec['last'] = datetime.utcnow().isoformat(timespec='seconds')
        return rec['count']

    # NGDimensionGacha.vecSummon is the *result* of a summon waiting to be
    # opened, not a shelf of cubes to browse.  DimensionGachaTabHeroUI.
    # GachaSummon @0x1A3C4D0 walks vecSummon and refuses to summon again --
    # "The previous progress of Portal was not complete." -- when entry 0
    # exists and SoldOut is false, and NGDimensionGacha.GetGachaCount
    # @0x1A51FD8 is nothing but vecSummon.Count.  So a summon puts one unsold
    # cube here, BuyDimensionGachaReq marks it sold, and the next summon is
    # free to run.
    def gacha_slots(self, banner):
        return self.d.setdefault('gacha_slots', {}).get(str(banner)) or []

    def set_gacha_result(self, banner, unit_id, rareness):
        """Record what a summon just rolled, as the one cube on offer."""
        cube = {'slot': 0, 'gacha': int(banner), 'unit': int(unit_id),
                'rareness': int(rareness), 'sold': False}
        self.d.setdefault('gacha_slots', {})[str(banner)] = [cube]
        return cube

    def take_gacha_slot(self, banner, slot_id):
        """Mark one cube opened; returns it, or None if it is gone/unknown."""
        for s in self.gacha_slots(banner):
            if int(s['slot']) == int(slot_id) and not s.get('sold'):
                s['sold'] = True
                return s
        return None

    def clear_gacha_results(self, banner=None):
        """Drop unopened cubes.  Done at login and whenever the summon screen
        is reopened, so a result the client never acknowledged cannot wedge
        every future summon behind the "previous progress" check."""
        slots = self.d.setdefault('gacha_slots', {})
        if banner is None:
            slots.clear()
        else:
            slots.pop(str(banner), None)

    # -- shop --------------------------------------------------------------
    def shop_bought(self, goods_uid):
        return int(self.d.setdefault('shop_buys', {}).get(str(goods_uid), 0))

    def add_shop_bought(self, goods_uid, n=1):
        buys = self.d.setdefault('shop_buys', {})
        buys[str(goods_uid)] = int(buys.get(str(goods_uid), 0)) + int(n)
        return buys[str(goods_uid)]

    # -- account rank ------------------------------------------------------
    # UserRankEXP gives the cumulative EXP for each rank (Rank 1 -> 300) and
    # the stamina cap that comes with it.  Nothing in the client tables says
    # what *grants* rank EXP, so this uses the stamina spent on a stage, which
    # is the usual convention and at least scales with content difficulty.
    # Change `add_user_exp`'s caller in handlers/dungeon.py to retune it.
    def add_user_exp(self, amount):
        self.d['exp'] = int(self.d.get('exp', 0)) + int(amount)
        return self.d['exp']

    def rank(self):
        from ..data.tables import TABLES, to_int
        exp, rank = int(self.d.get('exp', 0)), 1
        for row in TABLES.json('UserRankEXP'):
            need = to_int(row['EXP'], 0)
            if exp >= need:
                rank = to_int(row['Rank'], 1) + 1
            else:
                break
        return rank

    # -- progress ----------------------------------------------------------
    def is_cleared(self, dungeon_id):
        return str(dungeon_id) in self.d['cleared']

    def mark_cleared(self, dungeon_id, star_flag=7):
        k = str(dungeon_id)
        prev = int(self.d['cleared'].get(k, 0))
        self.d['cleared'][k] = prev | int(star_flag)
        return prev == 0          # True when this was a first clear
