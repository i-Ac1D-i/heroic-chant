"""The web dashboard: settings, saves, and device-to-account links.

Stdlib only, like the rest of the server -- `http.server` in a daemon thread
next to the asyncio game loop.  It is deliberately small: a JSON API plus one
static page.

**It has no authentication and can rewrite any save.**  So it binds
127.0.0.1 by default, and refuses a non-loopback bind unless a token is set
(`dashboard.token`, passed as ``?token=`` or an ``X-HC-Token`` header).  That is
enough for the phone-only case, where the only client is the browser on the
same device.

Everything the game reads goes through `SETTINGS` at the point of use, so a
change here takes effect on the next packet -- no restart.  The exception is a
save that is open in a live session: the player object is held in memory by
that session, so edits to a logged-in account are written to disk and picked up
when the client next reconnects.
"""
import copy
import json
import logging
import os
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from ..settings import SETTINGS, DEFAULTS
from ..game.player import Player, ACCOUNTS_DIR
from ..data.tables import TABLES, to_int
from .. import config

log = logging.getLogger('hc.web')

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
INDEX = os.path.join(os.path.dirname(ACCOUNTS_DIR), 'accounts', 'index.json')
ICONS = os.path.join(os.path.dirname(ACCOUNTS_DIR), 'icons')
HERO_ICONS = os.path.join(os.path.dirname(ACCOUNTS_DIR), 'hero_icons')

# A save is a few hundred KB; an import of one should never be more than a few
# MB. Anything larger is a mistake or an attack, and reading it would block the
# handler thread.
MAX_BODY = 32 * 1024 * 1024


# ----------------------------------------------------------------- helpers --
def _read_index():
    try:
        with open(INDEX, encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (IOError, OSError, ValueError):
        return {}


def _write_index(idx):
    os.makedirs(os.path.dirname(INDEX), exist_ok=True)
    tmp = INDEX + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(idx, fh, indent=1)
    os.replace(tmp, INDEX)


def _account_ids():
    try:
        names = os.listdir(ACCOUNTS_DIR)
    except OSError:
        return []
    return sorted(int(n[:-5]) for n in names
                  if re.fullmatch(r'\d+\.json', n))


def _devices_for(account_id, idx=None):
    idx = _read_index() if idx is None else idx
    return [d for d, a in idx.items() if int(a) == int(account_id)]


def _summary(account_id, idx):
    pl = Player.load(account_id)
    if pl is None:
        return None
    res = pl.d.get('resources', {})
    return {
        'account_id': account_id,
        'nickname': pl.d.get('nickname'),
        'exp': pl.d.get('exp', 0),
        'units': len(pl.d.get('units', [])),
        # Type 0 is Gold. Type 4 is User EXP -- an easy and previously-made
        # mistake; ResourceTable is the authority.
        'gold': int(res.get('0:-1:-1', 0) or 0),
        'resources': len(res),
        'last_login': pl.d.get('last_login'),
        'reg_date': pl.d.get('reg_date'),
        'devices': _devices_for(account_id, idx),
    }


def _key_parts(key):
    parts = [int(x) for x in str(key).split(':')]
    parts += [-1] * (3 - len(parts))
    return tuple(parts[:3])


def _account_name_keys(player_dict):
    """Every wallet key plus one per owned Relic/Artifact instance -- those
    are per-uid objects, not resource rows, but share the same name catalogue
    (resource_names() has an entry for each, keyed by their item id)."""
    keys = set(player_dict.get('resources', {}))
    keys.update('42:%d:-1' % r['id'] for r in player_dict.get('scenecards', []))
    keys.update('8:%d:-1' % a['id'] for a in player_dict.get('artifacts', []))
    return keys


def _name_map(keys):
    """{wallet key: display name} for exactly the keys asked about.

    Omits a key when there is no real name for it, rather than falling back
    to `resource_name()`'s generic "type 43 / 10" -- that string is meant for
    a log line or an error message, not a label the dashboard shows the user
    as if it meant something.
    """
    names = TABLES.resource_names()
    out = {}
    for key in keys:
        t1, t2, t3 = _key_parts(key)
        found = None
        for k in ((t1, t2, t3), (t1, t2, -1), (t1, -1, -1)):
            if k in names:
                found = ('%s %d' % (names[k], t2)) if k == (t1, -1, -1) and t2 >= 0 else names[k]
                break
        if found is None and t1 == 1:
            row = TABLES.unit(t2) or {}
            found = TABLES.strings().get(to_int(row.get('NameID'), -1))
        if found:
            out[key] = found
    return out


def _score(name, key, query):
    """Lower is better.  None means "does not match at all".

    Ranked rather than filtered because the catalogue is 4,650 rows and a short
    query matches a lot of them: searching `Q` for "Q's Memory" hits every name
    with a q in it.  The old version took the first 200 hits in table order and
    only *then* sorted, so the row the user wanted was usually not among them.
    That is the whole of "it is not even in the dashboard".
    """
    if not query:
        return 2
    low = name.lower()
    if low == query:                        # "q" -> "Q"
        return 0
    if key == query or key.startswith(query + ':'):
        return 0
    # A word starting with the query: "q" -> "Q's Memory", "Queen's Blade".
    if any(w.startswith(query) for w in re.split(r"[^a-z0-9]+", low) if w):
        return 1
    if low.startswith(query):
        return 1
    if key.startswith(query):
        return 2
    if query in low:
        return 3
    return None


# Type1 values that are owned things (gear, runes, costumes, chests, relics,
# artifacts) rather than a plain currency balance -- ResourceType names in
# enums.py: Item(5), Rune(6), Artifact(8), SceneCard(42), ItemRandomBox(48),
# TimeRewardRandomBox(60), CashBoxReward(113), ItemRandomBoxInven(120),
# Accessory(137), SelectBox(139), HeroSkin(153), WallPaper(158), Frame(170),
# StoryTreasureBox(164). Relics (42) and Artifacts (8) are per-instance
# objects with their own uid -- not stackable wallet rows at all -- so the
# picker sends them through add_relics/add_artifacts, never through
# resources; see _patch_account.
INVENTORY_T1 = {5, 6, 8, 42, 48, 60, 113, 120, 137, 139, 153, 158, 164, 170}

# Type1 1 is a raw hero name (ResourceType.Unit) -- redundant with the Roster
# tab's own picker, and not a wallet row a player can hold a quantity of, so
# neither catalogue offers it.
CATALOGUE_EXCLUDE_T1 = {1}


ITEM_T1 = 5     # ResourceType.Item -- gear, the one category with a real rarity
GRADE_LETTER = {1: 'C', 2: 'B', 3: 'A', 4: 'S', 5: 'SS'}


def _catalogue(query='', limit=200, kind=None, only_t1=None, grade=None):
    """Every nameable resource, for the add-a-resource picker.

    4,650 rows is too much to push at the browser on every load, so this is a
    search: the dashboard sends what the user typed.  An empty query still
    matches everything (`_score` gives it the weakest rank rather than no
    rank), so a category with no text is "browse the whole catalogue" -- the
    cut to `limit` happens *after* ranking, never before.  `kind` ('currency'
    or 'inventory') restricts which side of INVENTORY_T1 to search; `only_t1`
    narrows to one exact ResourceType (one Inventory category); `grade`
    (1-5, `itemList.itemGrade`, gear only) narrows to that rarity and sorts
    best-first instead of by text relevance -- browsing gear by rarity is not
    really a search.
    """
    query = (query or '').strip().lower()
    item_grades = None
    if only_t1 == ITEM_T1:
        item_grades = {to_int(r['itemID']): (to_int(r.get('itemGrade'), -1),
                                             to_int(r.get('viewStar'), 1))
                       for r in TABLES.sql('itemList')}

    scored = []
    for (t1, t2, t3), name in TABLES.resource_names().items():
        if t1 in CATALOGUE_EXCLUDE_T1:
            continue
        if kind == 'currency' and t1 in INVENTORY_T1:
            continue
        if kind == 'inventory' and t1 not in INVENTORY_T1:
            continue
        if only_t1 is not None and t1 != only_t1:
            continue
        item_grade, enhance = item_grades.get(t2, (-1, 1)) if item_grades is not None else (None, None)
        if grade is not None and item_grade != grade:
            continue
        key = '%d:%d:%d' % (t1, t2, t3)
        rank = _score(name, key, query)
        if rank is None:
            continue
        # Shorter names first inside a rank: for "q" that puts "Q's Memory"
        # above "Heart Heater's Quest House Contract". Browsing gear sorts
        # best rarity (then best +N) first instead -- rank is meaningless on
        # an empty query.
        sort_key = (-item_grade, -enhance, len(name), name.lower(), name, key) if item_grade is not None \
            else (rank, len(name), name.lower(), name, key)
        scored.append((sort_key, key, name, item_grade, enhance))
    scored.sort(key=lambda row: row[0])
    return [{'key': k, 'name': n,
             'kind': 'relic' if t1_of(k) == 42 else 'artifact' if t1_of(k) == 8 else 'resource',
             **({'grade': g, 'gradeLabel': '%s+%d' % (GRADE_LETTER.get(g, ''), e) if e else GRADE_LETTER.get(g, '')}
                if g is not None else {})}
            for _, k, n, g, e in scored[:limit]]


def t1_of(key):
    return int(key.split(':')[0])


def _gear(query='', slot=-1, limit=100):
    """Equippable items for one gear slot, ranked the same way resources are.

    `itemList.itemType` is the slot: 1 Weapon, 2 Armor, 3 Gloves, 4 Boots --
    the same numbers `NGUnitEquipInfo.ItemType` carries for gear, and the
    reason relics had to be given 7 and 8 instead.
    """
    strings = TABLES.strings()
    query = (query or '').strip().lower()
    scored = []
    for row in TABLES.sql('itemList'):
        item_slot = to_int(row.get('itemType'), -1)
        if slot >= 0 and item_slot != slot:
            continue
        item_id = to_int(row.get('itemID'), -1)
        if item_id < 0:
            continue
        name = strings.get(to_int(row.get('nameID'), -1)) or ('Item %d' % item_id)
        rank = _score(name, str(item_id), query)
        if rank is None:
            continue
        grade = to_int(row.get('itemGrade'), 0)
        # Best grade first inside a rank: browsing a slot with no query should
        # offer real gear, not the placeholder rows named "123".
        scored.append((rank, -grade, name.lower(), item_id, {
            'id': item_id, 'name': name, 'slot': item_slot, 'grade': grade}))
    scored.sort(key=lambda r: r[:4])
    return [r[4] for r in scored[:limit]]


def _mail_rewards(raw):
    """[{key: "t1:t2:t3", amount}] or [[t1, t2, t3, amount]] -> 4-tuples."""
    out = []
    for r in raw or []:
        if isinstance(r, dict):
            parts = list(_key_parts(str(r.get('key', ''))))
            amount = int(r.get('amount', 0))
        else:
            parts, amount = list(r[:3]), int(r[3])
        if len(parts) != 3 or amount <= 0 or int(parts[0]) < 0:
            raise ValueError('bad attachment %r' % (r,))
        out.append((int(parts[0]), int(parts[1]), int(parts[2]), amount))
    return out


def _send_mail(account_ids, body):
    from ..game import mail
    subject = str(body.get('subject') or '').strip()
    if not subject:
        raise ValueError('a post needs a subject')
    rewards = _mail_rewards(body.get('rewards'))
    hours = int(body.get('remove_hours') or mail.DEFAULT_REMOVE_HOURS)
    post = None
    for account_id in account_ids:
        post = mail.send(account_id, subject, str(body.get('contents') or ''),
                         rewards, sender=str(body.get('sender') or mail.SENDER),
                         remove_hours=hours)
    log.info('mail %r with %d attachment(s) sent to %d account(s)',
             subject, len(rewards), len(account_ids))
    return post


def _mailbox(account_id):
    from ..game import mail
    pl = Player.load(account_id)
    posts = (pl.d.get('posts') or []) if pl else []
    return {
        'pending': mail.pending(account_id),
        'mailbox': [{'uid': p.get('uid'), 'subject': p.get('subject'),
                     'received': bool(p.get('received')),
                     'rewards': [{'key': '%d:%d:%d' % tuple(r[:3]),
                                  'amount': r[3],
                                  'name': TABLES.resource_name(*r[:3])}
                                 for r in (p.get('rewards') or [])],
                     'reg': p.get('reg')}
                    for p in reversed(posts)][:50],
    }


def _clean_arena_bots(bots):
    """Run incoming bot teams through the arena's own validator.

    The dashboard is the only place a person types numbers that end up being
    fed straight to the battle scene, and the scene has no error handling: a
    hero in a slot that is not an `EUnitPosition` throws inside
    `PlayBaseScene.UnitLoading` and the player gets a black screen with
    nothing in any log they can see.  So what is stored is the repaired
    version, and the caller is told what was changed.
    """
    from ..game import arena
    if not isinstance(bots, list):
        return [], ['expected a list of teams']
    clean, warnings = [], []
    for i, bot in enumerate(bots):
        if not isinstance(bot, dict):
            warnings.append('team %d was not an object and was dropped' % (i + 1))
            continue
        fixed, problems = arena.check_bot(bot, i)
        warnings += ['%s: %s' % (fixed['name'], p) for p in problems]
        if not fixed['units']:
            continue
        # Store only what the settings file owns; index/custom are derived.
        clean.append({k: fixed[k] for k in
                      ('name', 'points', 'exp', 'profile',
                       'commander', 'commander_level', 'units')})
    return clean, warnings


def _unit_names():
    out = {}
    try:
        strings = TABLES.strings()
        for row in TABLES.units():
            uid = to_int(row.get('UnitID'), -1)
            if uid < 0:
                continue
            out[uid] = strings.get(to_int(row.get('NameID')), str(uid))
    except Exception:
        pass
    return out


def _unit_skin_ids():
    out = {}
    try:
        for row in TABLES.units():
            uid = to_int(row.get('UnitID'), -1)
            if uid >= 0:
                out[uid] = to_int(row.get('Skin_ID'), -1)
    except Exception:
        pass
    return out


def _player_hero_names():
    """Like `_unit_names()`, restricted to the real playable roster -- the
    other rows in UnitList are bosses, arena bots and the like, which have no
    portrait sprite and nowhere sensible to appear in the roster picker."""
    out = {}
    try:
        strings = TABLES.strings()
        for row in TABLES.units():
            if to_int(row.get('IsPlayerHero')) != 1:
                continue
            uid = to_int(row.get('UnitID'), -1)
            if uid >= 0:
                out[uid] = strings.get(to_int(row.get('NameID')), str(uid))
    except Exception:
        pass
    return out


# ----------------------------------------------------------------- handler --
class Handler(BaseHTTPRequestHandler):
    server_version = 'HeroicChant'
    token = ''

    # -- plumbing ----------------------------------------------------------
    def log_message(self, fmt, *args):
        log.debug('%s %s', self.address_string(), fmt % args)

    def _send(self, code, body=b'', ctype='application/json'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, indent=1).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _fail(self, code, message):
        self._send(code, {'error': message})

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0:
            return {}
        if n > MAX_BODY:
            raise ValueError('body too large (%d bytes)' % n)
        return json.loads(self.rfile.read(n).decode('utf-8'))

    def _authorised(self, query):
        if not self.token:
            return True
        given = (self.headers.get('X-HC-Token')
                 or (query.get('token') or [''])[0])
        return given == self.token

    # -- routing -----------------------------------------------------------
    def do_GET(self):
        self._route('GET')

    def do_POST(self):
        self._route('POST')

    def do_DELETE(self):
        self._route('DELETE')

    def _route(self, method):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        path = url.path.rstrip('/') or '/'
        if not self._authorised(query):
            return self._fail(401, 'bad or missing token')
        try:
            if path.startswith('/icons/'):
                return self._icon(path[len('/icons/'):])
            if path.startswith('/hero_icons/'):
                return self._hero_icon(path[len('/hero_icons/'):])
            if not path.startswith('/api/'):
                return self._static(path)
            return self._api(method, path[4:], query)
        except ValueError as exc:
            self._fail(400, str(exc))
        except Exception as exc:            # never take the dashboard down
            log.exception('dashboard %s %s failed', method, path)
            self._fail(500, '%s: %s' % (type(exc).__name__, exc))

    def _static(self, path):
        name = 'index.html' if path == '/' else path.lstrip('/')
        # Serve only what is actually in static/, resolved, so a crafted path
        # cannot walk out of it.
        full = os.path.realpath(os.path.join(STATIC, name))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            return self._fail(404, 'not found')
        ctype = {'.html': 'text/html; charset=utf-8',
                 '.js': 'text/javascript', '.css': 'text/css'}.get(
                     os.path.splitext(full)[1], 'application/octet-stream')
        with open(full, 'rb') as fh:
            self._send(200, fh.read(), ctype)

    def _icon(self, name):
        m = re.fullmatch(r'(-?\d+)_(-?\d+)\.png', name)
        if not m:
            return self._fail(404, 'not found')
        t1, t2 = m.group(1), m.group(2)
        if t1 == str(ITEM_T1):
            # Grade-up stages of the same gear chain share one look --
            # itemList.viewIMG, not the item's own id, is the real sprite key.
            row = TABLES.row('itemList', 'itemID', int(t2))
            if row is not None:
                t2 = str(to_int(row.get('viewIMG'), int(t2)))
        for size in ('m', 's'):
            for key in (t2, '-1'):
                full = os.path.realpath(os.path.join(ICONS, size, '%s_%s.png' % (t1, key)))
                if full.startswith(os.path.realpath(ICONS) + os.sep) and os.path.isfile(full):
                    with open(full, 'rb') as fh:
                        return self._send(200, fh.read(), 'image/png')
        return self._fail(404, 'not found')

    def _hero_icon(self, name):
        m = re.fullmatch(r'(\d+)\.png', name)
        if not m:
            return self._fail(404, 'not found')
        skin_id = _unit_skin_ids().get(int(m.group(1)), -1)
        full = os.path.realpath(os.path.join(HERO_ICONS, '%d.png' % skin_id))
        if not full.startswith(os.path.realpath(HERO_ICONS) + os.sep) or not os.path.isfile(full):
            return self._fail(404, 'not found')
        with open(full, 'rb') as fh:
            self._send(200, fh.read(), 'image/png')

    # -- the API -----------------------------------------------------------
    def _api(self, method, path, query):
        idx = _read_index()

        if path == '/status':
            return self._send(200, {
                'version': config.CLIENT_VERSION,
                'public_host': config.PUBLIC_HOST,
                'game_port': config.PORT,
                'accounts': len(_account_ids()),
                'devices': len(idx),
                'now': datetime.utcnow().isoformat(timespec='seconds'),
            })

        # ---- settings ----
        if path == '/settings':
            if method == 'GET':
                return self._send(200, {'defaults': DEFAULTS,
                                        'overrides': SETTINGS.overrides,
                                        'effective': SETTINGS.all})
            if method == 'POST':
                body = self._body()
                warnings = []
                if body.get('reset') is True:
                    SETTINGS.reset()
                elif 'reset' in body:
                    SETTINGS.reset(body['reset'])
                elif 'path' in body:
                    value = body.get('value')
                    if body['path'] == 'arena.bots':
                        value, warnings = _clean_arena_bots(value)
                    SETTINGS.set(body['path'], value)
                else:
                    incoming = body.get('settings') or {}
                    if isinstance(incoming.get('arena'), dict) \
                            and 'bots' in incoming['arena']:
                        incoming = copy.deepcopy(incoming)
                        incoming['arena']['bots'], warnings = \
                            _clean_arena_bots(incoming['arena']['bots'])
                    SETTINGS.update(incoming)
                log.info('settings updated via dashboard')
                if warnings:
                    log.info('arena bots corrected: %s', '; '.join(warnings))
                return self._send(200, {'effective': SETTINGS.all,
                                        'overrides': SETTINGS.overrides,
                                        'warnings': warnings})

        # ---- arena ----
        # The bot teams themselves live in settings (so they are edited and
        # reset like everything else); this endpoint is the read side, which
        # needs the game tables to turn ids into names and a team into a rating.
        if path == '/arena' and method == 'GET':
            from ..game import arena, artifacts
            units = _unit_names()
            shipped = arena._shipped_bots()
            custom = arena._custom_bots()

            def as_row(bot):
                return {
                    'index': bot['index'], 'name': bot['name'],
                    'points': bot['points'], 'tier': arena.tier_name(bot['points']),
                    'power': arena.bot_power(bot), 'custom': bot['custom'],
                    'units': [{'id': u['id'], 'name': units.get(u['id'], str(u['id'])),
                               'level': u['level'], 'tier': u['tier'],
                               'grade': u['grade'], 'rareness': u['rareness'],
                               'awaken': len(u.get('awaken') or []),
                               'gear': len([e for e in (u.get('equip') or [])
                                            if to_int(e, -1) > 0]),
                               'artifacts': list(u.get('artifacts')
                                                 or u.get('relics') or [])}
                              for u in bot['units']],
                }

            ladder = []
            for account_id in _account_ids():
                pl = Player.load(account_id)
                if pl is None:
                    continue
                rec = (pl.d.get('arena') or {})
                ladder.append({
                    'account_id': account_id, 'nickname': pl.d.get('nickname'),
                    'points': int(rec.get('points', 0)),
                    'tier': arena.tier_name(int(rec.get('points', 0))),
                    'wins': int(rec.get('wins', 0)),
                    'losses': int(rec.get('losses', 0)),
                    'defense': len(rec.get('defense') or []),
                    'power': arena.power(pl),
                })
            ladder.sort(key=lambda r: -r['points'])
            return self._send(200, {
                'players': ladder,
                'custom_bots': [as_row(b) for b in custom],
                'shipped_bots': len(shipped),
                'shipped_sample': [as_row(b) for b in shipped[:25]],
                # What the team builder is allowed to produce.  Sent rather
                # than hardcoded in the page so the rules have one home.
                'limits': {
                    'max_team': arena.MAX_TEAM,
                    'slots': list(arena.DEFAULT_SLOTS),
                    'grade_max': arena.GRADE_MAX,
                    'rareness_max': arena.RARENESS_MAX,
                    'name_max': arena.NAME_MAX,
                    'tier_caps': {str(t): lv for t, lv
                                  in sorted(arena.tier_level_caps().items())
                                  if t >= 1},
                    'heroes': {str(uid): {'name': name, 'rareness': rare,
                                          'nodes': len(arena.awaken_nodes(uid))}
                               for uid, (name, rare)
                               in sorted(arena.playable_heroes().items())},
                    # Wearable Artifacts, best grade first.  216 rows is small
                    # enough to send whole; gear is 2,526 and goes through
                    # /items instead.  These are Artifacts, not the Scene Cards
                    # the UI calls Relics.
                    'artifacts': [{'id': rid, 'grade': artifacts.grade_of(rid),
                                'slot': artifacts.slot_of(rid),
                                'name': TABLES.resource_name(8, rid)}
                               for rid in artifacts.catalogue()],
                    'gear_slots': {'1': 'Weapon', '2': 'Armor',
                                   '3': 'Gloves', '4': 'Boots'},
                },
                'tiers': [{'tier': to_int(t.get('Tier'), 0),
                           'name': arena.tier_name(to_int(t.get('PointMin'), 0)),
                           'from': to_int(t.get('PointMin'), 0)}
                          for t in arena.tiers()],
            })

        # ---- reference data, so the UI can show names not just numbers ----
        if path == '/reference' and method == 'GET':
            return self._send(200, {
                'units': _player_hero_names(),
                'banners': sorted({to_int(r.get('GachaID'), -1)
                                   for r in TABLES.sql('GachaList')} - {-1})[:60],
            })

        # Searchable resource catalogue for the wallet editor's picker.
        if path == '/resources' and method == 'GET':
            t1_raw = (query.get('t1') or [None])[0]
            grade_raw = (query.get('grade') or [None])[0]
            return self._send(200, {
                'resources': _catalogue((query.get('q') or [''])[0],
                                        int((query.get('limit') or ['200'])[0]),
                                        (query.get('kind') or [None])[0],
                                        int(t1_raw) if t1_raw not in (None, '') else None,
                                        int(grade_raw) if grade_raw not in (None, '') else None)})

        # Gear for the arena team builder.  Searched rather than sent whole --
        # itemList has 2,526 rows, against 216 relics which do go in /arena.
        if path == '/items' and method == 'GET':
            return self._send(200, {
                'items': _gear((query.get('q') or [''])[0],
                               to_int((query.get('slot') or ['-1'])[0], -1),
                               int((query.get('limit') or ['100'])[0]))})

        # ---- accounts ----
        if path == '/accounts' and method == 'GET':
            out = [s for s in (_summary(a, idx) for a in _account_ids()) if s]
            return self._send(200, {'accounts': out})

        m = re.fullmatch(r'/accounts/(\d+)', path)
        if m:
            account_id = int(m.group(1))
            if method == 'GET':
                pl = Player.load(account_id)
                if pl is None:
                    return self._fail(404, 'no such account')
                return self._send(200, {
                    'account': pl.d,
                    'names': _name_map(_account_name_keys(pl.d)),
                    'devices': _devices_for(account_id, idx)})
            if method == 'POST':
                return self._patch_account(account_id)
            if method == 'DELETE':
                path_ = Player.path(account_id)
                if os.path.exists(path_):
                    os.remove(path_)
                _write_index({d: a for d, a in idx.items()
                              if int(a) != account_id})
                log.info('deleted account %d via dashboard', account_id)
                return self._send(200, {'deleted': account_id})

        # ---- mail ----
        # Gifts go through the game's mail outbox, never into the save: an
        # online account's save is held in memory by its session and would be
        # overwritten.  See hc/game/mail.py.
        m = re.fullmatch(r'/accounts/(\d+)/mail', path)
        if m:
            account_id = int(m.group(1))
            if Player.load(account_id) is None:
                return self._fail(404, 'no such account')
            if method == 'GET':
                return self._send(200, _mailbox(account_id))
            if method == 'POST':
                post = _send_mail([account_id], self._body())
                return self._send(200, {'sent': 1, 'post': post,
                                        **_mailbox(account_id)})

        if path == '/mail' and method == 'POST':
            body = self._body()
            targets = body.get('accounts')
            if targets in (None, '', 'all'):
                targets = list(_account_ids())
            else:
                targets = [int(t) for t in targets]
            if not targets:
                return self._fail(400, 'no accounts to send to')
            post = _send_mail(targets, body)
            return self._send(200, {'sent': len(targets), 'post': post})

        m = re.fullmatch(r'/accounts/(\d+)/export', path)
        if m and method == 'GET':
            account_id = int(m.group(1))
            pl = Player.load(account_id)
            if pl is None:
                return self._fail(404, 'no such account')
            blob = {
                'heroic_chant_save': 1,
                'exported': datetime.utcnow().isoformat(timespec='seconds'),
                'client_version': config.CLIENT_VERSION,
                'account': pl.d,
                'devices': _devices_for(account_id, idx),
            }
            body = json.dumps(blob, indent=1).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Disposition',
                             'attachment; filename="heroic-chant-%d.json"' % account_id)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            return self.wfile.write(body)

        if path == '/accounts/import' and method == 'POST':
            return self._import_account(idx)

        # ---- device links ----
        if path == '/devices':
            if method == 'GET':
                return self._send(200, {'devices': idx})
            if method == 'POST':
                body = self._body()
                device = str(body.get('device_id') or '').strip()
                if not device:
                    raise ValueError('device_id is required')
                account_id = body.get('account_id')
                if account_id in (None, '', -1):
                    idx.pop(device, None)
                else:
                    idx[device] = int(account_id)
                _write_index(idx)
                log.info('device %s -> account %s', device, account_id)
                return self._send(200, {'devices': idx})

        return self._fail(404, 'no such endpoint: %s' % path)

    # -- account mutation --------------------------------------------------
    def _patch_account(self, account_id):
        """Apply a partial edit.  Anything absent is left alone."""
        pl = Player.load(account_id)
        if pl is None:
            return self._fail(404, 'no such account')
        body = self._body()

        for key in ('nickname', 'exp', 'represent_profile', 'skin_id'):
            if key in body:
                pl.d[key] = body[key]

        # resources: {"4:-1:-1": 999} -- absolute values, 0 removes the row
        for key, value in (body.get('resources') or {}).items():
            parts = [int(x) for x in str(key).split(':')]
            parts += [-1] * (3 - len(parts))
            k = '%d:%d:%d' % tuple(parts[:3])
            if int(value) == 0:
                pl.d['resources'].pop(k, None)
            else:
                pl.d['resources'][k] = int(value)

        # units: add by unit id, or remove by uid
        for uid in (body.get('remove_units') or []):
            pl.d['units'] = [u for u in pl.d['units'] if int(u['uid']) != int(uid)]
        for spec in (body.get('add_units') or []):
            if isinstance(spec, dict):
                pl.add_unit(int(spec['id']), level=int(spec.get('level', 1)),
                            tier=int(spec.get('tier', 1)),
                            grade=int(spec.get('grade', 1)),
                            rareness=spec.get('rareness'))
            else:
                pl.add_unit(int(spec))
        for patch in (body.get('update_units') or []):
            unit = pl.find_unit(int(patch['uid']))
            if unit is None:
                continue
            for f in ('level', 'tier', 'grade', 'rareness'):
                if f in patch:
                    unit[f] = int(patch[f])

        # Relics and Artifacts are per-instance objects with their own uid,
        # not stackable wallet rows, so "give one" means minting a new
        # instance -- same shape as add_units, {"id": ..., "count": ...}.
        for spec in (body.get('add_relics') or []):
            item_id = spec['id'] if isinstance(spec, dict) else spec
            count = max(int(spec.get('count', 1)), 1) if isinstance(spec, dict) else 1
            for _ in range(count):
                pl.add_scenecard(int(item_id))
        for uid in (body.get('remove_relics') or []):
            pl.remove_scenecard(int(uid))
        for spec in (body.get('add_artifacts') or []):
            item_id = spec['id'] if isinstance(spec, dict) else spec
            count = max(int(spec.get('count', 1)), 1) if isinstance(spec, dict) else 1
            for _ in range(count):
                pl.add_artifact(int(item_id))
        for uid in (body.get('remove_artifacts') or []):
            pl.remove_artifact(int(uid))

        # Anything not covered above: a raw merge, so the dashboard is never a
        # narrower editor than a text editor on the same file.
        for key, value in (body.get('raw') or {}).items():
            pl.d[key] = value

        pl.save()
        log.info('account %d edited via dashboard', account_id)
        return self._send(200, {'account': pl.d,
                                'names': _name_map(_account_name_keys(pl.d))})

    def _import_account(self, idx):
        body = self._body()
        blob = body.get('save') if isinstance(body.get('save'), dict) else body
        data = blob.get('account') if isinstance(blob.get('account'), dict) else blob
        if not isinstance(data, dict) or 'resources' not in data:
            raise ValueError('that does not look like a Heroic Chant save')

        # Where it lands: an explicit target, else the id it came from, else a
        # fresh one.  Importing onto a live device link is the whole point of
        # the feature -- moving a save to a new phone -- so it is allowed, but
        # it overwrites, and the caller has to say so.
        target = body.get('account_id')
        if target in (None, ''):
            target = data.get('account_id')
        target = int(target) if target not in (None, '') else (
            max(_account_ids(), default=1000) + 1)

        if os.path.exists(Player.path(target)) and not body.get('overwrite'):
            raise ValueError('account %d already exists -- pass overwrite:true '
                             'or choose another id' % target)

        data = dict(data)
        data['account_id'] = target
        pl = Player(data)
        pl.save()

        device = str(body.get('device_id') or '').strip()
        if device:
            idx[device] = target
            _write_index(idx)
        log.info('imported save into account %d%s', target,
                 ' for device %s' % device if device else '')
        return self._send(200, {'account_id': target,
                                'devices': _devices_for(target)})


# ------------------------------------------------------------------- boot --
def start(bind=None, port=None, token=None):
    """Start the dashboard in a daemon thread.  Returns the server, or None."""
    bind = bind if bind is not None else SETTINGS.get('dashboard.bind', '127.0.0.1')
    port = int(port if port is not None else SETTINGS.get('dashboard.port', 8099))
    token = token if token is not None else SETTINGS.get('dashboard.token', '')

    loopback = bind in ('127.0.0.1', 'localhost', '::1')
    if not loopback and not token:
        log.error('refusing to serve the dashboard on %s with no token -- it '
                  'can rewrite any save. Set dashboard.token, or bind '
                  '127.0.0.1.', bind)
        return None

    Handler.token = token or ''
    try:
        httpd = ThreadingHTTPServer((bind, port), Handler)
    except OSError as exc:
        log.error('dashboard could not bind %s:%d (%s)', bind, port, exc)
        return None
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name='hc-dashboard').start()
    log.info('dashboard on http://%s:%d%s', bind, port,
             '  (token required)' if token else '')
    return httpd
