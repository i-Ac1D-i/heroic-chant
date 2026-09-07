"""End-to-end test for the config dashboard.

    python tools/test_dashboard.py

Starts the dashboard on a spare port against a throwaway accounts directory and
settings file, drives the real HTTP API, and checks that a setting changed
through the API actually changes what the game code does -- which is the only
part that matters and the easy part to break.

Touches nothing in server/accounts or server/settings.json.
"""
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s  %s%s' % ('PASS' if cond else 'FAIL', name,
                          '' if cond else ('  -- ' + str(detail))))


def call(base, path, method='GET', body=None):
    req = urllib.request.Request(
        base + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={} if body is None else {'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {'error': raw[:200].decode('utf-8', 'replace')}


def main():
    sandbox = tempfile.mkdtemp(prefix='hc-dash-')
    accounts = os.path.join(sandbox, 'accounts')
    os.makedirs(accounts)
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')

    # Point the player module at the sandbox before anything caches it.
    from hc.game import player as player_mod
    player_mod.ACCOUNTS_DIR = accounts
    from hc.webui import server as web
    web.ACCOUNTS_DIR = accounts
    web.INDEX = os.path.join(accounts, 'index.json')

    import hc.handlers.center as center
    center.ACCOUNTS_DIR = accounts
    center.INDEX = os.path.join(accounts, 'index.json')

    from hc.settings import SETTINGS
    from hc.game import rewards, gacha
    from hc.game.player import Player

    print('sandbox:', sandbox)
    port = int(os.environ.get('HC_TEST_WEB_PORT', '18101'))
    httpd = web.start(bind='127.0.0.1', port=port, token='')
    if httpd is None:
        print('could not start the dashboard on port %d' % port)
        return 2
    base = 'http://127.0.0.1:%d/api' % port

    try:
        print('\napi')
        code, st = call(base, '/status')
        check('status responds', code == 200 and 'version' in st, st)
        code, body = call(base, '/settings')
        check('settings expose defaults + overrides',
              code == 200 and 'defaults' in body and 'effective' in body, body)
        code, body = call(base, '/nope')
        check('unknown endpoint 404s', code == 404, code)

        print('\nsettings reach the game code')
        call(base, '/settings', 'POST', {'settings': {'rewards': {'multiplier': 4.0}}})
        SETTINGS.load()
        check('reward multiplier applies',
              rewards._scaled([(4, -1, -1, 10)], False) == [(4, -1, -1, 40)],
              rewards._scaled([(4, -1, -1, 10)], False))
        call(base, '/settings', 'POST',
             {'settings': {'gacha': {'cost': {'1': {'type1': 99, 'type2': -1,
                                                    'single': 7, 'ten': 60}}}}})
        SETTINGS.load()
        check('summon cost override applies', gacha.cost(1, 1) == (99, -1, 7),
              gacha.cost(1, 1))
        call(base, '/settings', 'POST', {'reset': 'gacha.cost'})
        SETTINGS.load()
        check('resetting one key restores the table',
              gacha.cost(1, 1) == (33, -1, 5), gacha.cost(1, 1))
        call(base, '/settings', 'POST', {'reset': True})
        SETTINGS.load()
        check('reset-all restores defaults',
              SETTINGS.get('rewards.multiplier') == 1.0,
              SETTINGS.get('rewards.multiplier'))

        print('\nitem names')
        from hc.data.tables import TABLES
        check('gold is named from ResourceTable',
              TABLES.resource_name(0, -1, -1) == 'Gold', TABLES.resource_name(0, -1, -1))
        check('type 4 is User EXP, not gold',
              TABLES.resource_name(4, -1, -1) == 'User EXP', TABLES.resource_name(4, -1, -1))
        check("a hero's own shard names the hero",
              TABLES.resource_name(16, 41, -1).endswith("'s Memory"),
              TABLES.resource_name(16, 41, -1))
        check('gear is named', 'Weapon' in TABLES.resource_name(5, 1, -1),
              TABLES.resource_name(5, 1, -1))
        check('an unknown key degrades honestly',
              TABLES.resource_name(9999, -1, -1) == 'type 9999',
              TABLES.resource_name(9999, -1, -1))
        code, body = call(base, '/resources?q=memory&limit=20')
        check('the catalogue is searchable',
              code == 200 and body['resources']
              and all('memory' in r['name'].lower() for r in body['resources']),
              body)

        print('\naccounts')
        # A full account is slow to build (138 heroes); a bare one is enough.
        SETTINGS.set('account.grant_all_heroes', False)
        SETTINGS.load()
        # Mirror the login path: the device index is written by
        # account_for_device, not by Player.create, so a save made any other
        # way is not linked to anything.
        first = center.account_for_device('dev-a')
        Player.create(first, 'dev-a')
        check('a first-seen device mints its own account', first == 1001, first)
        # The rest of the test edits this account under a fixed id.
        os.rename(Player.path(first), Player.path(2001))
        idx = json.load(open(center.INDEX, encoding='utf-8'))
        idx['dev-a'] = 2001
        json.dump(idx, open(center.INDEX, 'w', encoding='utf-8'))
        pl = Player.load(2001)
        pl.d['account_id'] = 2001
        pl.save()
        check('account created', os.path.exists(Player.path(2001)))
        code, body = call(base, '/accounts')
        check('account is listed',
              code == 200 and any(a['account_id'] == 2001 for a in body['accounts']),
              body)

        code, body = call(base, '/accounts/2001', 'POST',
                          {'nickname': 'Edited', 'resources': {'4': 4242},
                           'add_units': [{'id': 1, 'level': 9}]})
        check('edit applies', code == 200
              and body['account']['nickname'] == 'Edited'
              and body['account']['resources']['4:-1:-1'] == 4242
              and len(body['account']['units']) == 1, body.get('error'))

        on_disk = json.load(open(Player.path(2001), encoding='utf-8'))
        check('edit is persisted', on_disk['nickname'] == 'Edited')

        code, exported = call(base, '/accounts/2001/export')
        check('export looks like a save',
              code == 200 and exported.get('heroic_chant_save') == 1
              and 'resources' in exported['account'], code)

        code, body = call(base, '/accounts/import',
                          'POST', {'save': exported, 'account_id': 2002,
                                   'device_id': 'dev-b'})
        check('import lands on a new id', code == 200 and body['account_id'] == 2002, body)
        code, body = call(base, '/accounts/2002')
        check('imported save carries the data',
              code == 200 and body['account']['nickname'] == 'Edited'
              and body['account']['account_id'] == 2002, body.get('error'))
        check('import linked the device', 'dev-b' in body.get('devices', []),
              body.get('devices'))

        code, body = call(base, '/accounts/import', 'POST',
                          {'save': exported, 'account_id': 2002})
        check('import refuses to clobber without overwrite', code == 400, code)

        code, body = call(base, '/accounts/2001')
        check('account payload carries item names',
              code == 200 and body.get('names', {}).get('4:-1:-1') == 'User EXP',
              body.get('names'))

        print('\nduplicate heroes')
        pl = Player.load(2001)
        pl.add_unit(41, rareness=2)
        unit, shards = gacha.grant(pl, 41, 2)
        check('an SS duplicate pays the hero their own Memory',
              unit is None and shards == [(16, 41, -1, 40)], shards)
        unit, shards = gacha.grant(pl, 41, 3)
        check('an SSS duplicate pays out at all (DecompPieceReturn has no row)',
              unit is None and shards == [(16, 41, -1, 40)], shards)
        unit, shards = gacha.grant(pl, 41, 0)
        check('a lower-rarity duplicate still uses DecompPieceReturn',
              shards and shards[0][0] == 45, shards)
        call(base, '/settings', 'POST',
             {'settings': {'gacha': {'duplicate': {'2': [[16, 'unit', -1, 7]]}}}})
        SETTINGS.load()
        unit, shards = gacha.grant(pl, 41, 2)
        check('the "unit" placeholder resolves to the summoned hero',
              shards == [(16, 41, -1, 7)], shards)
        call(base, '/settings', 'POST', {'reset': 'gacha.duplicate'})
        SETTINGS.load()

        # A dashboard bot goes straight into the battle scene, which has no
        # error handling, so the API must repair what it is given rather than
        # store it as typed.
        print('\narena bot validation over the API')
        code, body = call(base, '/settings', 'POST', {
            'path': 'arena.bots',
            'value': [{'name': 'Bad', 'points': 1000, 'units': [
                {'id': 1, 'level': 60, 'tier': 5, 'grade': 3},
                {'id': 16, 'level': 62, 'tier': 5, 'grade': 5},
                {'id': 87, 'level': 1, 'tier': 5, 'grade': 5},
                {'id': 2, 'level': 999, 'tier': 5, 'grade': 7}]}]})
        stored = body['effective']['arena']['bots']
        check('the API stores the repaired team, not what was sent',
              [u['slot'] for u in stored[0]['units']] == [1, 2, 10, 11],
              stored[0]['units'])
        check('the API says what it changed',
              any('grade' in w for w in body['warnings'])
              and any('level' in w for w in body['warnings']), body.get('warnings'))
        code, body = call(base, '/arena')
        lim = body.get('limits') or {}
        check('the builder is told the rules',
              lim.get('max_team') == 4 and lim.get('slots') == [1, 2, 10, 11]
              and lim.get('tier_caps', {}).get('5') == 80
              and len(lim.get('heroes') or {}) == 138, lim.get('slots'))
        call(base, '/settings', 'POST', {'reset': 'arena.bots'})
        SETTINGS.load()

        print('\ndevice links')
        code, body = call(base, '/devices')
        check('both devices are listed',
              code == 200 and body['devices'].get('dev-a') == 2001
              and body['devices'].get('dev-b') == 2002, body)
        call(base, '/devices', 'POST', {'device_id': 'dev-c', 'account_id': 2001})
        code, body = call(base, '/devices')
        check('a device can be re-pointed', body['devices'].get('dev-c') == 2001, body)
        call(base, '/devices', 'POST', {'device_id': 'dev-c', 'account_id': None})
        code, body = call(base, '/devices')
        check('a device can be unlinked', 'dev-c' not in body['devices'], body)

        # Separate accounts per device is the whole point of the index.
        check('a new device gets its own account',
              center.account_for_device('dev-fresh') not in (2001, 2002))

        print('\nsafety')
        code, _ = call(base.replace('/api', ''), '/../settings.json')
        check('static serving will not walk out of static/', code == 404, code)
        blocked = web.start(bind='0.0.0.0', port=port + 1, token='')
        check('refuses a public bind with no token', blocked is None)

    finally:
        httpd.shutdown()
        shutil.rmtree(sandbox, ignore_errors=True)

    print()
    if FAIL:
        print('%d FAILED, %d passed' % (len(FAIL), len(PASS)))
        for f in FAIL:
            print('   -', f)
        return 1
    print('ALL CHECKS PASSED (%d)' % len(PASS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
