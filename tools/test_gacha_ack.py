"""Checks on the shape of JoinDimensionGachaAck.

    python tools/test_gacha_ack.py

The summon Ack is the one packet whose *shape* is known to matter: the fuller
version makes the client throw a NullReferenceException and kills the summon
button until restart, and the safe version differs from it in five independent
ways.  `HC_GACHA_ACK_PARTS` exists to add those back one at a time on a real
device.  This pins down what each part actually puts on the wire, so the
experiment stays honest and a later refactor cannot quietly change what is
being tested.

Runs entirely in a sandbox directory; touches nothing in server/accounts.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s  %s%s' % ('PASS' if cond else 'FAIL', name,
                          '' if cond else ('  -- ' + str(detail))))


def with_parts(value):
    """Set HC_GACHA_ACK_PARTS (None clears it) and drop HC_GACHA_RICH_ACK."""
    os.environ.pop('HC_GACHA_RICH_ACK', None)
    if value is None:
        os.environ.pop('HC_GACHA_ACK_PARTS', None)
    else:
        os.environ['HC_GACHA_ACK_PARTS'] = value


def listlen(dto, field):
    return len(getattr(dto, field, None) or [])


def main():
    sandbox = tempfile.mkdtemp(prefix='hc-gachaack-')
    accounts = os.path.join(sandbox, 'accounts')
    os.makedirs(accounts)
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')

    from hc.game import player as player_mod
    player_mod.ACCOUNTS_DIR = accounts
    from hc.game.player import Player
    from hc.handlers import gacha as gh
    from hc.protocol.dto import TYPES, encode_packet, decode_packet

    try:
        print('\nHC_GACHA_ACK_PARTS parsing')
        with_parts(None)
        check('unset means the safe shape', gh._ack_parts() == frozenset())
        with_parts('all')
        check('"all" turns everything on',
              gh._ack_parts() == frozenset(gh.ACK_PARTS))
        with_parts('dimension')
        check('a single part is honoured', gh._ack_parts() == {'dimension'})
        with_parts(' Units , BANNER ')
        check('parts are trimmed and case-insensitive',
              gh._ack_parts() == {'units', 'banner'})
        with_parts('dimension,nonsense')
        check('an unknown part is dropped, not fatal',
              gh._ack_parts() == {'dimension'})
        with_parts(None)
        os.environ['HC_GACHA_RICH_ACK'] = '1'
        check('HC_GACHA_RICH_ACK=1 still means everything',
              gh._ack_parts() == frozenset(gh.ACK_PARTS))
        os.environ.pop('HC_GACHA_RICH_ACK', None)

        print('\nwhat each part puts on the wire')
        p = Player.create(900001, 'test-device-gacha-ack')
        banner = gh.gacha.BANNERS[0]
        # A pull as the handler leaves it: a cube waiting to be opened, a
        # dirtied wallet, and one hero that was genuinely new.
        p.set_gacha_result(banner, p.d['units'][0]['id'], 3)
        p.bump_summon_count(banner, 1)
        added = [p.d['units'][0]]
        one_key = next(iter(p.resource_items()))[:3]
        full_wallet = len(list(p.resource_items()))

        def shape(parts):
            with_parts(parts)
            p._dirty = {one_key}
            p._dirty_collections = set()
            return gh.summon_ack_shape(p, banner, added)

        safe, safe_count = shape(None)
        check('safe shape sends no dimension',
              listlen(safe, 'vecChangeDimensionGacha') == 0)
        check('safe shape announces no unit',
              listlen(safe, 'vecAddUnitInfo') == 0)
        check('safe shape reports GachaID -1', safe_count.GachaID == -1,
              safe_count.GachaID)
        check('safe shape sends the whole wallet',
              listlen(safe, 'vecAddResourceInfo') == full_wallet,
              '%d of %d' % (listlen(safe, 'vecAddResourceInfo'), full_wallet))
        check('the wallet is worth diffing', full_wallet > 1, full_wallet)

        dim, _ = shape('dimension')
        check('"dimension" adds exactly one banner',
              listlen(dim, 'vecChangeDimensionGacha') == 1)
        # The client dereferences both of these per entry with no skip path
        # (JoinDimensionGachaAck @0x14EC1EC and @0x14EC2F4), so a null in
        # either is a guaranteed NullReferenceException on the device.
        entries = dim.vecChangeDimensionGacha
        check('every dimension carries a vecSummon list',
              all(getattr(e, 'vecSummon', None) is not None for e in entries))
        slots = [s for e in entries for s in (e.vecSummon or [])]
        check('the pending cube is in it', len(slots) == 1, len(slots))
        check('every cube carries a ProductInfo',
              bool(slots) and all(getattr(s, 'ProductInfo', None) is not None
                                  for s in slots))
        check('"dimension" alone still sends the whole wallet',
              listlen(dim, 'vecAddResourceInfo') == full_wallet)

        _, banner_count = shape('banner')
        check('"banner" reports the real banner id',
              banner_count.GachaID == banner, banner_count.GachaID)

        units, units_count = shape('units')
        check('"units" announces the new hero',
              listlen(units, 'vecAddUnitInfo') == 1)
        check('"units" alone leaves GachaID at -1', units_count.GachaID == -1)

        delta, _ = shape('wallet')
        check('"wallet" sends only what changed',
              listlen(delta, 'vecAddResourceInfo') == 1,
              listlen(delta, 'vecAddResourceInfo'))

        every, every_count = shape('all')
        check('"all" is the pre-workaround Ack',
              listlen(every, 'vecChangeDimensionGacha') == 1
              and listlen(every, 'vecAddUnitInfo') == 1
              and listlen(every, 'vecAddResourceInfo') == 1
              and every_count.GachaID == banner)

        print('\nthe screen-open shape is untouched')
        with_parts('all')
        p._dirty, p._dirty_collections = {one_key}, set()
        opened, open_count = gh.summon_ack_shape(p, -1, [])
        check('opening the screen ignores the parts entirely',
              listlen(opened, 'vecChangeDimensionGacha') == 0
              and open_count.GachaID == -1)

        print('\nevery shape survives the marshaller')
        for parts in (None, 'wallet', 'collections', 'units', 'dimension',
                      'banner', 'all'):
            sync, cnt = shape(parts)
            body = encode_packet(40067, 0, sync, cnt, TYPES['NGPairInt2'](),
                                 TYPES['NGIntIntInt'](),
                                 TYPES['NGGachaChoiceCubeInfo']())
            back = decode_packet(40067, body)
            check('%s encodes and reads back clean' % (parts or 'safe'),
                  back['_trailing'] == 0, back['_trailing'])
    finally:
        with_parts(None)
        shutil.rmtree(sandbox, ignore_errors=True)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        print('FAILED: ' + ', '.join(FAIL))
        return 1
    print('ALL CHECKS PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
