# Contributing to Heroic Chant

Most of the work here is the same shape: the client sends a packet, nothing
answers it, you make something answer it. This doc gets you to the point where
you can do that.

## Getting set up

You need your own copy of the game. Nothing game-derived is in this repo and it
should stay that way.

What you need:

- `com.ngelgames.herocantare` 1.2.389 APK
- the game's `files/ngelgames/` directory pulled off a device (this is where
  the AssetBundles live, ~1.9 GB)
- `herocantare.db`, the decrypted script tables

Then:

```bash
pip install -r requirements-dev.txt
python tools/gen_protocol.py --apk /path/to/herocantare.apk
python tools/extract_assets.py
python tools/selftest.py
```

If the selftest passes you're good. It spins up the real server, drives a fake
client through login, a stage clear and a level-up, and checks the numbers.

The server itself has zero dependencies. `requirements-dev.txt` is only for the
tools.

## Layout

```
hc/
  net.py              TCP, framing, packet dispatch
  main.py             entry point
  config.py           settings, all overridable with HC_* env vars

  protocol/
    wire.py           framing, XOR, zlib. every rule cites the address it came from
    spec.json         859 packets + 180 structs (generated, gitignored)
    dto.py            reads spec.json, gives you encode/decode

  data/
    tables.py         the game's 424 data tables, SQLite + JSON, cached

  game/               game logic, no networking
    player.py         a save file
    state.py          the DTO builders every handler uses
    rewards.py        stage drops
    equipment.py      gear and runes
    gacha.py          summoning
    guild.py          guilds
    shop.py           the shop
    afk.py            City Search
    enums.py          the client's own enums (generated)
    errors.py         Ack error codes

  handlers/           one file per feature, this is where you'll be
tools/                everything that reads the APK or drives a device
```

The rule that keeps it untangled: `game/state.py` doesn't import any feature
module, and feature modules import `state`. If you find yourself writing
`from . import something` *inside* a function to dodge a circular import, the
code is in the wrong file.

## The only concept you need

A handler is a coroutine registered against a packet id:

```python
@handler(30005)
async def unit_decomp(s, a):
    ...
```

`s` is the session (`s.player` is the save). `a` is a dict of the packet's
arguments, keyed by the same names the client's own function signature uses.
You don't parse anything — `spec.json` knows the layout, `dto.py` does the work.

To find a packet's shape:

```python
python -c "
import sys; sys.path.insert(0,'.')
from hc.protocol.dto import PACKET_SPEC
print(PACKET_SPEC[30005])"
```

## Worked example: decomposing heroes

`UnitDecompReq` (30005) isn't implemented. It's a good first one. Here's the
whole thing.

**1. Look at the packet and its reply.**

```
30005  UnitDecompReq(List<long> vecDecompUnit)
40010  UnitDecompAck(int Error, List<long> vecDecompUnit, NGCheckServerInfo _CheckInfo)
```

So: the client sends unit UIDs, we delete them, refund something, reply.

**2. Find the data.** Nearly everything is already in a table. Grep the table
list for a likely name:

```python
python -c "
import sys; sys.path.insert(0,'.')
from hc.data.tables import TABLES
print([t for t in TABLES.sql_tables() if 'Return' in t])"
```

That gives `RemoveLvUpReturn`, `RemoveTierReturn`, `RemoveGradeReturn` — refunds
by level, by tier, by grade. Look at a row to see the columns. A level 5 hero
refunds 23 of resource type 2 (level-up material); a level 1 hero refunds type
`-1`, which is how every table in this game spells "nothing".

**3. Write it.** New file or an existing one in `hc/handlers/`:

```python
@handler(30005)
async def unit_decomp(s, a):
    """Break heroes down for the materials that went into them."""
    p = s.player
    removed, refund = [], []

    for uid in a['vecDecompUnit']:
        unit = p.find_unit(uid)
        if unit is None:
            continue
        row = TABLES.row('RemoveLvUpReturn', 'Lv', unit.get('level', 1))
        # -1 means "nothing" all over these tables, not "resource id -1"
        if row and to_int(row['ResourceType1'], -1) >= 0:
            refund.append((to_int(row['ResourceType1']), -1, -1,
                           to_int(row['ResourceVal1'], 0)))
        removed.append(state.unit_info(unit))
        p.d['units'].remove(unit)

    rewards.grant(p, refund)
    p.save()
    log.info('decomposed %d unit(s)', len(removed))

    await s.send(40010, Err.OK, a['vecDecompUnit'],
                 state.resource_sync(p, vecDelUnitInfo=removed))
```

**4. Register it** — add the module to `hc/handlers/__init__.py` if it's new.

**5. Try it.** Restart, play, watch the log.

That's the pattern. Read the args, change the save, tell the client what
changed.

## Things worth knowing

**`resource_sync(player)` is how the client learns anything changed.** It
gathers up every resource you touched since the last sync and packs it into the
`NGCheckServerInfo` that almost every Ack ends with. Extra lists go in as
kwargs:

```python
state.resource_sync(p, vecChangeUnitInfo=[state.unit_info(unit)])
```

**Those lists are absolute, not deltas.** `vecAddResourceInfo` says "you now
have 500 gold", not "+500". The name lies. The client works out the "+N" popup
itself by diffing. Send deltas and every currency in the game drifts.

**Never send a null nested struct.** A null list is fine (writes a count of 0),
but a null struct throws inside the client's own marshaller. Build the empty
one: `TYPES['NGFrameInfo']()`.

**Handler crashes get caught and logged**, not propagated, because a
mid-session disconnect leaves the client on a black screen with no way back. So
if something silently does nothing, read the log — your exception is in there.

**Save on every change.** `p.save()` writes the whole JSON file. Cheap enough
at this scale, and it means a crash doesn't cost the player anything.

## Gotchas that have already cost someone a day

These are all written up properly in [docs/PROTOCOL.md](docs/PROTOCOL.md) and
[docs/STATUS.md](docs/STATUS.md). Skim them before you spend hours on
something weird.

- **Field order on the wire isn't declaration order.** Some declared fields are
  never serialised at all. Don't read layouts off `dump.cs`.
- **List counts are int16.** Strings are int32-length UTF-8. `DateTime` is six
  int16s.
- **Collection counter keys default `Type3` to −1, not 0.** Stage progression
  lives in those counters, and a key with `Type3 = 0` silently never matches.
  This one cost a whole session.
- **`ClearType` is how a stage was played, not whether it was won.** There's no
  defeat packet anywhere. If `DungeonEndReq` arrives, they won.
- **Some "display name" fields are numeric string-table ids.** Put a real string
  in `NGServerGroupInfo.ServerName` and the client throws a `FormatException`
  and login just stops, with nothing on screen.
- **On Windows, a stale server keeps answering on a port a new one thinks it
  owns** (`SO_REUSEADDR`). Both servers bind exclusively now, but if something
  behaves like it's running old code, check for orphan processes first.

## Testing

`python tools/selftest.py` before you push. It's not a unit test suite, it's an
end-to-end run against a real socket, which for this project catches more.

Beyond that, test in the game. Most bugs here are "the client didn't like
that", and only the client can tell you.

Turn on `--log-level DEBUG` to see every packet in both directions.

## Finding something to work on

Run the server, play, and collect these:

```
C2S GameServerC2S.SomeThingReq -- no handler (24 body bytes)
```

343 packets are in that state. The ones the game hits first are the ones worth
doing first.

Bigger things that need actual design:

- **PvP / arena** — needs a second player or an AI stand-in. `ArenaAIList` and
  `ArenaAITeamInfo` exist in the data, so the game shipped with bot teams.
- **Guild raids and wars** — currently stubbed to empty replies.
- **The Store screen** only shows its Costume tab. The listings are all served
  correctly, so the tab list is coming from somewhere else. Unsolved.
- **The gacha result screen** doesn't close. The hero is granted and saved, the
  client just sits waiting for something more in the Ack.
- **Multiplayer at all.** Right now a device id maps straight to an account with
  no auth, saves are whole-file rewrites with no locking, and `DungeonEndReq` is
  taken on trust. Fine solo, wide open in public.

## Style

Match what's there. Four spaces, no type annotations, docstrings on anything
non-obvious.

Comment the *why*, not the *what*. The valuable comments in this codebase are
the ones explaining why the code looks wrong but isn't — the addresses, the
lying field names, the client quirks. When you work something out by staring at
disassembly, write it down next to the code or it's lost.
