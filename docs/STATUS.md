# Heroic Chant — build log and findings

A running record of what got built, in what order, and what broke on the way.
Most of the value here is the "what broke" -- several of these cost a full
session to work out and would be invisible to anyone reading only the code.

Newest sections are at the bottom.

## First milestone: the game runs

Logged in, reached the lobby with the full 138-hero roster, entered STORY mode,
and played stage 1-1 to completion with real turn-based combat.

Proof in `docs/screenshots/`:

* `lobby.jpg`  — the lobby: level, gold, stamina, full menu
* `teamup.jpg` — party select with a 4-hero line-up and the 1-1 Battle button
* `battle.jpg` — turn 2 of the actual fight, chain skills and buffs running
* `stage-select-progression.jpg` — 1-1 OPEN, 1-2 playable, 1-3 CLOSE
* `battle-1-2.jpg` — self-picked party, full AUTO, no tutorial rails
* `hero-levelup.jpg` — Mori Jin Lv.2/30 with recomputed stats

Everything below is what was set up, what was fixed to get there, and how to
bring it back after a reboot.

---

## 1. Bring it back up (after a reboot)

MuMu Player was running; a reboot clears the `adb reverse` tunnels and the two
servers, but **not** the emulator-side setup (app data, hosts file, patched
config all persist).

One command does the whole thing:

```bash
cd server && python tools/resume.py
```

That reconnects adb, re-adds the port forwards, starts both servers, and
launches the game. If you would rather do it by hand:

```bash
/c/platform-tools/adb.exe connect 127.0.0.1:16384
/c/platform-tools/adb.exe -s 127.0.0.1:16384 root
/c/platform-tools/adb.exe -s 127.0.0.1:16384 reverse tcp:80 tcp:8080
/c/platform-tools/adb.exe -s 127.0.0.1:16384 reverse tcp:21010 tcp:21010
```

```bash
python tools/bootserver.py --host 127.0.0.1 --port 21010 --http-port 8080 --no-dns
```

```bash
python -m hc.main --public-host 127.0.0.1 --port 21010
```

**The `adb reverse` lines are the part people forget.** Without them the client
resolves the game hostnames to `127.0.0.1` (its own loopback) and finds nothing.

---

## 2. What is already done on the emulator

Persistent — no need to redo unless you wipe the emulator.

| item | state |
|---|---|
| MuMu Player | Android 12 (SDK 32), ABI list includes `arm64-v8a`, Magisk root |
| adb | `127.0.0.1:16384` (7555 and 5555 also answer — same device) |
| Client | `com.ngelgames.herocantare` 1.2.389, `primaryCpuAbi=arm64-v8a` |
| App data | 1.9 GB copied from `/sdcard/Download/data/files` into `/data/data/com.ngelgames.herocantare/files`, `chown u0_a57:u0_a57`, `restorecon -R` |
| `/system/etc/hosts` | 7 ngelgames hostnames → `127.0.0.1` |
| On-device config | `dnsinfo.json`, `real_serverinfo.json`, `real_patchinfo12389.json` patched to `127.0.0.1:21010` (`.bak` files kept alongside) |
| Account | created on first contact, saved under `server/accounts/` |

### Why loopback instead of a LAN IP

Binding port 80 and adding firewall rules needs Administrator on Windows.
Routing through `adb reverse` sidesteps both: the device talks to its own
`127.0.0.1:80` and adb tunnels it to the host's `8080`. It also keeps working
when the LAN IP changes.

For the LAN-IP route (which you need for a real phone rather than an emulator),
use an elevated terminal, pass `--host <your-lan-ip>` everywhere, re-patch the
device config with `tools/patch_client_config.py`, and open the firewall.

---

## Second pass: progression fixed, played to 1-2

Cleared **1-1 and 1-2**, both 3 stars, the second one on full AUTO with a
self-picked 4-hero party and no tutorial hand-holding. Rewards, currencies,
stamina and stage unlocks all match the server exactly, and progression now
survives a relogin.

The blocker was subtle and worth remembering: **`Type3int64` initialises all
three key components to −1**, and the two-argument `GetKey(a, b)` that
`CheckClearDungeon` uses fills in only Type1 and Type2. The lookup key is
therefore `(DungeonClearCount, dungeonID, **-1**)`. Storing counters with
`Type3 = 0` made every lookup miss silently: the game played fine and paid out
fine, but no stage ever registered as cleared, so the next one stayed locked
and first-clear rewards were offered forever. Full write-up in
`docs/PROTOCOL.md` §9.

Progression lives entirely in these collection counters -- there is no "cleared
stages" list anywhere in the 859 packets.

**Zero unhandled packets** across the whole login → lobby → stage-select →
team-up → battle → results → level-up flow.

### Account rank gates hero levels

Tapping level-up produced only *"Heroes can only reach the same level as your
Account Level after level 30. Claim City Search rewards to increase your
Account Level."* — the control levels a hero straight to 30, and account rank
capped it. Account rank comes from `UserRankEXP` (rank 1 → 300 EXP, rank 30 →
157 800, and its `ticketMax` column is the stamina cap shown as the `/11`).

Nothing in the client tables says what *grants* rank EXP — retail fed it from
City Search claims, which this server does not implement — so
`handlers/dungeon.py` now grants the stage's stamina cost as rank EXP, and new
accounts start at the rank-30 threshold so hero levelling is not blocked out of
the gate. Both are reconstructions and are commented as such; set
`Player.create`'s `exp` to 0 for a from-scratch progression run.

With that, **`UnitLevelUpReq` works live**: Mori Jin went to Lv.2, −500 gold and
−190 mats, exactly the `LevelUpTable` row, and the client's stat panel updated
(ATK 147→165, HP 1126→1257) with the next cost reading 1,400/500 as the table
says.

### Known cosmetic gap

The per-floor star counter still shows 0/30 rather than 6/30, so
`NormalDungeonStarCount`'s exact key shape is unconfirmed — `(29, floor, -1)`
and `(29, storySeason, floor)` were both tried. It gates the star-reward chests
only; stage progression is unaffected.

## Third pass: rarity, gacha, star-up, awakening, shop

All verified against the real client unless noted.

### Hero rarity was stuck on A

`Player.add_unit` defaulted `rareness` to 0, so the entire roster read as A.
It comes from `UnitList.Rareness`, and the real scale has **four** tiers, not
three: 0 = A, 1 = S, 2 = SS, **3 = SSS**. A full roster comes out 13 A / 28 S / 93 SS / 4 SSS,
and the Hero Info grid now shows the pink SSS and orange SS badges.

### Gacha

The roll engine reads `GachaList` / `GachaInfo` / `GachaSummonGroupInfo` /
`unitGachaID` / `unitRateGroup` and reproduces the live rates -- banner 2 gives
**2.5% SS / 13% S / 84.5% A**. Pulls grant real heroes and persist.

Three things had to be worked out to get the Time Cube to fire at all:

1. **Banner 1 (the cube the screen shows) has no rows in `unitGachaID`**, so it
   could never produce a hero. It now falls back to banner 2's pools.
2. **`NGDimensionGacha.vecSummon` must stay empty while idle.** Pre-filling the
   cubes made the client refuse the screen with *"The previous progress of
   Portal was not complete."* -- it reads unsold slots as an unfinished
   transaction.
3. **The tap *is* the pull.** `JoinDimensionGachaReq` carries the banner in
   `openGachaID` (-1 = screen opened, 1 = pulling); `actionType` stays 0 either
   way, so gating on it meant the pull never happened.

Known gap: the pull succeeds and the hero is granted, but the client's result
page sits on *"Waiting for server response..."* -- it wants something more in
the ack than `NGGachaChoiceCubeInfo`. Close and reopen the screen and the new
hero is there.

Also: the **PrimiumHero** tab (the real banner list) is hidden because
`PremiumUnitCreate` is **empty in this data dump** -- one of only three empty
tables. That is a data gap, not a server bug.

### Star-up and Awakening

Both driven entirely by the shipped tables and both confirmed in-game on
Pinocchian (SSS):

* **Star-up** (`UnitGradeUpReq`) costs `GradeUpMaterial` rows keyed by the
  unit's *current* grade -- elemental Shards (13/x), Memory Stones (45/x by
  letter) and, from 3 stars, the hero's own Memory. Went ★★ -> ★★★.
* **Awakening** (`UnitAwakenReq`) is the per-hero skill tree: each node is a
  `partsInfoID` with its own `unitAwakenOpenCost`. Unlocked nodes persist on the
  unit and replay through `NGUnitInfo.vecAwakenInfo`. Node 10001 opened, the
  client showed *"You successfully increased the skill's Stats!"* and Power went
  632 -> 628 exactly as the preview said.

New accounts now start with Shards, Memory Stones and awakening stones so both
systems are usable immediately.

### Shop (lowest priority -- server done, UI partly)

`ShopInfo` / `ShopGoodsInfo` / `CountPrice` are served through `LogInAck05`,
`GetScriptShopInfoReq` and `GetLimitedShopInfoReq`; purchase charges the
escalating `CountPrice` and grants the goods. 490 listings for our region.

Every real sale window closed in 2021-2023, so `hc/game/shop.py` advertises an
open window instead (`OPEN_FROM` / `OPEN_UNTIL`). Even so the Store screen only
renders its **Costume Shop** tab -- its tab list evidently comes from somewhere
other than the listings we serve. Not chased further.

### Two more transport bugs

* `NGScriptShopInfo.vecPeriodTime` is a **list** of DateTime, not a scalar. The
  "vec" prefix is the tell. Passing a bare datetime threw inside the encoder.
* A **stale `selftest.py` still held port 21099** and kept answering, so new
  runs silently talked to old code. Same Windows `SO_REUSEADDR` shadowing that
  bit the boot shim; `hc/net.py` now binds exclusively on Windows so a clash
  fails loudly. `selftest.py` also times out instead of hanging forever.

## 3. Bugs found and fixed while getting it running

These were all real, and all are committed.

1. **`ServerName` must be a number.** `AuthScene.OnGetServerGroup` @0x1E906B4
   does `int.Parse(NGServerGroupInfo.ServerName)`. Sending `"HC" (well, the game full name)`
   threw a `FormatException` inside the client and login silently stalled with
   no visible error. It is a string-table id: 34645 = "Global". Now
   `config.SERVER_NAME_STRING_ID`.
2. **`ClearType` is not a win/lose flag.** It is a play mode
   (Default/Fast/Immediate/Clear). There is no retreat or defeat packet in any
   of the 401 C2S ids — a loss just returns to the lobby and the server never
   hears about it, with the cost already charged at DungeonStart. So *any*
   `DungeonEndReq` is a clear. My first version gated on `ClearType == 3` and
   paid out nothing for a normal win.
3. **Boot shim was too narrow.** The client fetches far more over HTTP than the
   two config files: `assetList.json.gz`, then AssetBundles at
   `.../Android/<sub/path>` (including nested ones like
   `Android/sound/voice/ch3011`), then movies at `movie/story/*.mp4` which live
   in `ngelgames/movie/`, not under `AssetBundle/`. All now served.
4. **Windows `SO_REUSEADDR` let two shims bind port 8080 simultaneously**, with
   the stale one silently answering some requests — which looked exactly like a
   code bug for a while. `allow_reuse_address` is now off on Windows so a
   second instance fails loudly.
5. **`NGPairInt64Int` fields are `first`/`second`**, not `Value1`/`Value2`. The
   `TotalUnitPowerInfoReq` handler crashed on it.
6. **A handler exception killed the whole session.** The dispatcher now catches
   and logs, because the client cannot recover from a mid-session disconnect —
   it just sits on a black screen.
7. **Collection counters were keyed with `Type3 = 0` instead of −1**, so stage
   progression never registered (see §2b).
8. **Nothing sent the counters at login**, so even correct clears were forgotten
   on the next launch. `NGLoginAckLargeData.vecCollectionInfo` now carries them,
   and `NGCheckServerInfo.vecAddCollectionInfo` carries changes.

Also note: **Git Bash mangles absolute paths** in adb arguments
(`/data/data/...` → `C:/Program Files/Git/data/data/...`). Prefix commands with
`MSYS_NO_PATHCONV=1`.

---

## 4. Verified working end to end

```
connected → handshake (hostID) → GetServerGroupInfoReq/Ack
  → GetConnectGameServerInfoReq/Ack → reconnect → LogInReq
  → LogInAck01..05 + LoginAckLargeData (9691 bytes)
  → lobby, 138 heroes, 5,000,000 gold, 999 stamina
  → DungeonSceneJoinReq → DungeonStartReq (stage 1, −5 stamina)
  → real turn-based battle → DungeonEndReq
```

The client decoded every packet, including a 1658-byte
`TotalUnitPowerInfoReq` carrying all 138 units — which is strong independent
confirmation that the DTO layouts extracted from the binary are correct.

**34 of 401 C2S packets have handlers**, and the whole login → lobby →
stage-select → team-up → battle → results → hero level-up flow now produces
**zero** `no handler` lines.

---

## 5. Next thing to do (superseded -- reward payout is now confirmed)

Restart and clear stage 1-1 again. With fix #2 in place the payout path now
actually runs, and that is the one part of the loop not yet confirmed against
the real client — the server-side reward logic is tested (`tools/selftest.py`),
but a live clear has not paid out yet.

Watch for:

```
hc.dungeon  stage 1 cleared (first=True, stars=N, ClearType=1): N drops, N unlocks
```

Then keep playing and collect `no handler` lines — they name exactly what to
implement next, in the order the game actually needs them. Known outstanding
from the session: `GetLatestHottimeInfoReq` was hit, plus whatever the post-
battle result screen and the hero/inventory menus ask for.

Adding one is usually five lines; see the example in the README.

---

## 6. Files worth knowing about

```
server/accounts/1001.json     your save -- plain JSON, editable, restart client to reload
server/accounts/index.json    device id -> account id
docs/PROTOCOL.md              the wire format, with RVAs for every claim
docs/TESTING.md               generic setup guide (LAN-IP route, real phones)
docs/STATUS.md                this file
tools/resume.py               one-shot: adb forwards + both servers + launch
```

Logs from the last run were at `%TEMP%\game.log` and `%TEMP%\boot.log`;
`tools/resume.py` writes to `server/logs/` instead so they survive.


## Fourth pass: equipment, guilds, and a refactor for contributors

### Equipment

Gear turned out to be stackable and to live in the ordinary resource wallet as
`ResourceType.Item` (5) keyed by itemID -- the same channel stage drops use.
What a unit *wears* is separate: a per-unit slot -> itemID map, replayed to the
client through `NGUnitInfo.vecEquipInfo`.

Because gear stacks, equipping has to move a copy out of the wallet, or one
sword could be worn by the whole roster. `equip`/`unequip` keep that balanced;
`equipment.CONSUME_ON_EQUIP` turns it off if you'd rather gear be free.

Verified in-game with Auto Equip -> "Equip Damage Set": four slots filled and
the client recomputed ATK 236 -> 554 and HP 1717 -> 3226, and it survived a
relogin. Item upgrade (`itemList` fusion chains with per-mille success rolls),
selling (`ResourceTable.SellGold`) and runes are in too.

### Guilds

Implemented as a **guild of one**: you found it, you own it, you're the only
member. That's enough to light up creation, info, comments, donations, the buff
board and the exchange shop without inventing a social layer the server can't
back. Anything needing other players -- wars, PvP, raids, invites -- answers
empty but well-formed, so those screens open instead of hanging.

All driven by the shipped tables: `GuildInfo` (1,000,000 gold to found,
100,000/donation, 5/day), `GuildLevelInfo`, `GuildBuffInfo`, `GuildExchange`.

### Refactor

`state.py` had grown into a 286-line grab-bag of builders for six different
features, with `from . import x` inside functions to dodge circular imports.
Split so each feature owns its own DTO builders and `state.py` keeps only the
core (resources, counters, units, `NGCheckServerInfo`). The import graph now
goes one way: features import `state`, `state` imports nothing back.

Also:

- `hc/game/errors.py` -- the `Err.OK` / `NOT_ENOUGH` / ... names that replaced
  ~100 magic `0`/`1`/`2` literals in handler replies
- `TABLES.row()` / `TABLES.rows()` -- cached single-row lookups, replacing
  hand-rolled linear scans that re-walked 4,832-row tables per request
- `tools/gen_enums.py` -- enum generation is reproducible now, and pulls in the
  client's real 453-value `NMError`
- game-derived data is gitignored; the tools rebuild all of it
