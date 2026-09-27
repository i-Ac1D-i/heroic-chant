"""Daily ticket top-ups, from the client's own ResourceRefresh table.

Each row names a resource (ResourceType1/2), a SeasonValue -- 1 is the Daily
season -- and how much comes back each season (SeasonAddResourceCount) up to a
cap (MaxResourceCount).  The rows that matter today:

    26     Hero Dungeon ticket         +10, cap 10
    28/g   Dimension Crack ticket      +3,  cap 3, one wallet per tower (1..5)
    30     Arena ticket                +5,  cap 5
    39     Alien ticket                +10, cap 10
    116    Roguelike quest plays       +2,  cap 2

A top-up only ever raises a wallet: new accounts start with 99 Hero Dungeon
and Arena tickets, and those are kept.  The table also has ChargeTime (one
back every N minutes) for a couple of rows; that is not done here.

Each resource remembers the Daily season it was last topped up in
(``player.d['refills']``), so calling this more than once a day is free.
Switch it off with the ``tickets.daily_refill`` setting.
"""
from ..data.tables import TABLES, to_int
from ..settings import SETTINGS
from . import state

DAILY = 1          # ResourceRefresh.SeasonValue for the Daily season


def daily_rows():
    """[(type1, type2, add, cap)] for every row that refills daily."""
    out = []
    for r in TABLES.json('ResourceRefresh'):
        if to_int(r.get('SeasonValue'), -1) != DAILY:
            continue
        cap = to_int(r.get('MaxResourceCount'), 0)
        add = to_int(r.get('SeasonAddResourceCount'), 0)
        if cap > 0 and add > 0:
            out.append((to_int(r['ResourceType1']), to_int(r.get('ResourceType2'), -1),
                        add, cap))
    return out


def top_up(player, now=None):
    """Refill whatever has not been refilled this Daily season.  Returns the
    [(type1, type2, amount)] actually added."""
    if not SETTINGS.get('tickets.daily_refill', True):
        return []
    season = state.season_value(state.SEASON_DAILY, now)
    done = player.d.setdefault('refills', {})
    added = []
    for t1, t2, add, cap in daily_rows():
        key = '%d:%d' % (t1, t2)
        if int(done.get(key, -1)) == season:
            continue
        done[key] = season
        have = player.get_resource(t1, t2)
        n = min(have + add, cap) - have
        if n > 0:
            player.add_resource(t1, n, t2)
            added.append((t1, t2, n))
    return added
