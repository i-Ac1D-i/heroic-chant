"""Static game data.

Two sources, both shipped with the client:

* ``table/herocantare.db``  -- 172 SQLite tables (units, skills, items,
  artifacts, runes, scene cards, gacha, guild, localisation).  This is the
  decrypted form of the ``script/unit`` AssetBundle, whose TextAssets are
  wrapped in an "adong:" AES envelope.
* ``server/data/*/*.json``  -- 252 JSON tables lifted straight out of the
  ``script/dungeon``, ``script/resource``, ``script/tutorial`` and
  ``script/test`` bundles by ``tools/extract_assets.py``.

Every column in both sources is a string; helpers here coerce on read.
"""
import json
import os
import sqlite3
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The defaults assume the PC layout, where `table/` sits next to `server/`.
# HC_DATA_DIR / HC_DB_PATH override that for setups whose tree looks different
# -- a phone under Termux, most obviously, where everything lands in one place.
DATA_DIR = os.environ.get('HC_DATA_DIR') or os.path.join(ROOT, 'data')
DB_PATH = (os.environ.get('HC_DB_PATH')
           or os.path.join(os.path.dirname(ROOT), 'table', 'herocantare.db'))


def to_int(v, default=-1):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


class Tables(object):
    def __init__(self, db_path=None, data_dir=None):
        self.db_path = db_path or DB_PATH
        self.data_dir = data_dir or DATA_DIR
        self._db = None
        self._sql_cache = {}
        self._json_cache = {}
        self._index_cache = {}

    # -- SQLite side -------------------------------------------------------
    @property
    def db(self):
        if self._db is None:
            if not os.path.exists(self.db_path):
                raise IOError('herocantare.db not found at %s' % self.db_path)
            self._db = sqlite3.connect(self.db_path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
        return self._db

    def sql(self, table):
        """Whole SQLite table as a list of dicts (cached)."""
        if table not in self._sql_cache:
            cur = self.db.execute('SELECT * FROM "%s"' % table)
            self._sql_cache[table] = [dict(r) for r in cur.fetchall()]
        return self._sql_cache[table]

    def sql_tables(self):
        cur = self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [r[0] for r in cur.fetchall()]

    # -- JSON side ---------------------------------------------------------
    def json(self, name):
        """A JSON table by bare name, e.g. ``dungeonlist`` or ``ShopInfo``."""
        if name in self._json_cache:
            return self._json_cache[name]
        for sub in ('dungeon', 'resource', 'tutorial', 'test'):
            path = os.path.join(self.data_dir, sub, name + '.json')
            if os.path.exists(path):
                with open(path, encoding='utf-8-sig') as fh:
                    self._json_cache[name] = json.load(fh)
                return self._json_cache[name]
        raise KeyError('no JSON table named %r under %s' % (name, self.data_dir))

    def json_tables(self):
        out = []
        for sub in ('dungeon', 'resource', 'tutorial', 'test'):
            d = os.path.join(self.data_dir, sub)
            if os.path.isdir(d):
                out += [f[:-5] for f in sorted(os.listdir(d)) if f.endswith('.json')]
        return out

    # -- indexing ----------------------------------------------------------
    def index(self, name, key, unique=True, source='json'):
        """Index a table by one column.  Returns {int(key): row} or {key: [rows]}."""
        ck = (source, name, key, unique)
        if ck in self._index_cache:
            return self._index_cache[ck]
        rows = self.json(name) if source == 'json' else self.sql(name)
        if unique:
            idx = {}
            for r in rows:
                idx[to_int(r[key])] = r
        else:
            idx = defaultdict(list)
            for r in rows:
                idx[to_int(r[key])].append(r)
            idx = dict(idx)
        self._index_cache[ck] = idx
        return idx

    def row(self, name, column, value, source='sql'):
        """One row by column value, cached.

        Feature code used to hand-roll `for r in TABLES.sql(...)` scans; on
        tables like ShopInfo (4 832 rows) that adds up fast when it runs per
        request. This builds the index once and reuses it.
        """
        return self.index(name, column, source=source).get(to_int(value))

    def rows(self, name, column, value, source='sql'):
        """Every row matching a column value, cached."""
        return self.index(name, column, unique=False,
                          source=source).get(to_int(value), [])

    # -- convenience lookups used by the game handlers ---------------------
    def dungeon(self, dungeon_id):
        return self.index('dungeonlist', 'dungeon_ID').get(int(dungeon_id))

    def dungeon_cost(self, dungeon_id):
        """(cost_type, cost_amount) for a normal/hard story stage, else (-1, 0)."""
        for tbl in ('normalStoryDungeonList', 'hardStoryDungeonList'):
            row = self.index(tbl, 'dungeon_ID').get(int(dungeon_id))
            if row:
                return to_int(row.get('cost_Type1')), to_int(row.get('cost_Val1'), 0)
        return -1, 0

    def clear_rewards(self, dungeon_id):
        """Rows from rewardClear for one stage (both repeat and first-clear)."""
        return self.index('rewardClear', 'dungeonID', unique=False).get(int(dungeon_id), [])

    def unit(self, unit_id):
        return self.index('UnitList', 'UnitID', source='sql').get(int(unit_id))

    def units(self):
        return self.sql('UnitList')

    def strings(self, lang='English'):
        """String table for the client's UI language; keyed by numeric id."""
        tbl = 'hc_string_%s' % lang
        rows = self.sql(tbl)
        key = list(rows[0].keys())[0]
        val = list(rows[0].keys())[1]
        return {to_int(r[key]): r[val] for r in rows}

    # -- naming a wallet row ------------------------------------------------
    # A save's wallet is keyed by (Type1, Type2, Type3) and nothing else, which
    # makes it unreadable: "16:41" is Bam's Memory and "5:1403" is a specific
    # sword.  `ResourceTable` is the client's own answer -- 4,650 rows of
    # (ResourceID, Type2, Type3) -> NameID -- and covers all but a handful.
    # itemList and runeList fill the rest in, and unnamed keys fall back to
    # something honest rather than a lie.
    def resource_names(self):
        """(t1, t2, t3) -> display name, built once and cached."""
        if getattr(self, '_res_names', None) is not None:
            return self._res_names
        strings = self.strings()
        out = {}
        for r in self.json('ResourceTable'):
            name = strings.get(to_int(r.get('NameID'), -1))
            if name:
                out[(to_int(r.get('ResourceID'), -9), to_int(r.get('Type2'), -1),
                     to_int(r.get('Type3'), -1))] = name
        # Items and runes are keyed by their own id in Type2 and mostly appear
        # in ResourceTable already; these catch the ones that do not.
        for row in self.sql('itemList'):
            key = (5, to_int(row.get('itemID'), -1), -1)
            if key not in out:
                name = strings.get(to_int(row.get('nameID'), -1))
                if name:
                    out[key] = name
        for row in self.sql('runeList'):
            key = (6, to_int(row.get('runeID'), -1), -1)
            if key not in out:
                name = strings.get(to_int(row.get('nameID'), -1))
                if name:
                    out[key] = name
        self._res_names = out
        return out

    def resource_name(self, t1, t2=-1, t3=-1):
        """The best name for one wallet key.  Never returns None."""
        t1, t2, t3 = int(t1), int(t2), int(t3)
        names = self.resource_names()
        for key in ((t1, t2, t3), (t1, t2, -1), (t1, -1, -1)):
            if key in names:
                # A (t1, -1, -1) hit for a keyed resource is the family name,
                # not this row's -- say which member it is.
                if key == (t1, -1, -1) and t2 >= 0:
                    return '%s %d' % (names[key], t2)
                return names[key]
        if t1 == 1:                                  # ResourceType.Unit
            row = self.unit(t2) or {}
            name = self.strings().get(to_int(row.get('NameID'), -1))
            if name:
                return name
        return 'type %d' % t1 if t2 < 0 else 'type %d / %d' % (t1, t2)


TABLES = Tables()
