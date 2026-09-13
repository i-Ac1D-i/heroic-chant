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

## Fifth pass: the phone installer, actually run

`tools/termux-setup.sh` was the one thing here a stranger runs unattended, on
hardware we don't have, and it had never been executed start to finish -- only
`bash -n`'d. `tools/test-termux-setup.sh` now rehearses it on a desktop: every
Termux-only command stubbed onto `PATH`, `HOME` in a sandbox, downloads off a
local range-capable HTTP server, and the "repo" a throwaway clone of the
working tree. Seven scenarios, 40 assertions. Run it before touching the
installer.

Doing that turned up four real bugs, three of them in the shipped script.

### `read` does not work in a `curl | bash` script

The advertised install is a pipe, so **stdin is the script itself**. When the
script hit

```sh
read -r -p "  Press Enter once the install has finished... " _
```

bash had already parsed the enclosing `if ... fi` compound, so `read` consumed
whatever line came next in the file and returned immediately. The prompt never
waited. Reproduced exactly: pipe a script with `echo 1; read x; echo 2` into
bash and line 2 vanishes.

Today the line after that `fi` happens to be blank, so nothing else broke --
but the next candidate is `cat > "$BASE/start.sh" <<'LAUNCHER'`, and eating
*that* would dump the entire launcher body into the live shell. It was one
edit away from being much worse.

Now it prompts on `/dev/tty`, and where there is no terminal (or
`HC_NONINTERACTIVE=1`) it polls `installed()` for up to five minutes instead.
Either way it confirms the package actually arrived before moving on.

The whole script body is also wrapped in `{ ... }` now, which forces bash to
parse the entire file before running any of it -- so a connection that drops
mid-download can't leave a half-executed install.

### Downloads did not resume across runs

`need() { [ ! -s "$1" ]; }` treated any non-empty file as finished. A 2 GB
transfer killed at 300 MB left a non-empty `ngelgames.zip`; the next run said
"client files already here", skipped `verify` entirely, and handed a truncated
zip to the boot server. `docs/MOBILE.md` claimed resume worked; `curl -C -`
only ever resumed within a single run's retries.

Downloads now land on `<dest>.part` and are renamed only once curl says it is
done, so a `.part` is unambiguously a resume point. `have()` replaced `need()`
and takes a minimum size, and `verify()` enforces the same floor -- a truncated
zip still starts with `PK`, so magic bytes alone wave half a file through.
Floors are `HC_FILES_MIN` / `HC_DB_MIN` / `HC_DATA_MIN` / `HC_APK_MIN`.

Google Drive does answer `206 Partial Content`, verified against all three
large files, so the resume path is real and not theoretical.

### `start.sh` exposed the game server to the LAN

`config.BIND_HOST` defaults to `0.0.0.0` and `start.sh` never passed `--host`.
The boot shim already passed `--bind 127.0.0.1`; the game server was missed. On
a phone that put a save server with no authentication, which takes every packet
on trust, on whatever wifi you happened to be on. Both now bind loopback.

### `kill 0` in start.sh's exit trap

The launcher's cleanup was `kill 0`, which signals the entire process group.
Run from an interactive Termux prompt that is exactly the two servers, which is
why it looked fine. Launched from anything else -- a script, a test harness --
it takes the parent down with it. Found because it killed the test harness
mid-run and looked like a hang. Now it kills the two recorded child pids.

### And one in the harness worth remembering

The Windows fallback for "stop the servers" was

```
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'bootserver.py|hc\.main' }
```

which matches **the powershell process running that query**, because the
pattern is right there in its own command line. It killed the whole shell tree.
Filter on `Name -eq 'python.exe'` first.

## Sixth pass: the summon crash, and a config dashboard

### The Dimension Gacha wedge

Summoning showed the hero, then a *"The Dimension has dissipated"* popup, then a
"connecting to server" overlay over the OK button that could not be dismissed --
the app had to be restarted, once per pull.

The cause, from the client's own log (`adb logcat -d | grep Unity`):

```
RmiSend : 30062   DimensionGachaTabHeroUI:GachaSummon()
NGNetGameServer OK, Object reference not set to an instance of an object.
```

`JoinDimensionGachaAck` was throwing inside the client. `GachaSummon`
@0x1A3C3E4 opens with `if (bSendPacket) return;` and that flag is only cleared
once the Ack is fully processed, so a throw mid-Ack kills the summon button for
the rest of the session. That is the whole wedge.

**Proving the Ack was the trigger took a delay, not a guess.** The device clock
and the server clock are ~1.75 s apart, which is the same order as the gap
being measured, so the logs could be read either way. `HC_GACHA_ACK_DELAY=8`
holds the Ack back; the throw moved with it, landing 8.09 s after the request.
Worth remembering: **never compare a logcat timestamp with a server timestamp
without establishing the offset first.**

What it was *not* -- each ruled out by a live test, all of them plausible:

* `_GachaChoiceCubeInfo` (a previous session had invented values for it)
* `vecAddUnitInfo`
* `vecChangeDimensionGacha`
* the reward contents

**Read that list carefully, because it is weaker than it looks.** Every one of
those was tested by *removing* it from the fuller Ack and leaving the rest in.
That shows none of them is individually *necessary*; it says nothing about
which is *sufficient*. If two of the fields can each throw on their own,
subtracting one at a time can never find either. See the eighth pass.

What works is answering a summon with the same Ack *shape* as a screen-open.
The claim that this differed from the natural Ack in two ways was wrong -- it
differs in five. `HC_GACHA_ACK_PARTS` now adds them back one at a time.
Remaining cost: a genuinely *new* hero is not announced, so it only shows after
a relogin. Duplicates are unaffected.

Still open: the "dissipated" popup itself. It is now dismissable and the pull is
saved, so it is cosmetic.

### Two real bugs found on the way

**Duplicates were minting second copies of heroes.** With a full roster every
pull is a duplicate, and the test account had drifted to 145 units for 138
heroes. `DecompPieceReturn` is the conversion table, keyed by the duplicate's
rarity (rareness 0/1/2 -> ResourceType 45/1, 45/2, 45/3, x1). `gacha.grant`
now checks ownership.

**`vecSummon` is not "always empty".** The handoff said it was, because
pre-filling it produced *"The previous progress of Portal was not complete."*
The actual rule, from `GachaSummon` @0x1A3C4D0, is that the client refuses only
when `vecSummon[0]` exists **and `SoldOut` is false** -- an unclaimed cube from
a previous summon. `NGDimensionGacha.GetGachaCount` @0x1A51FD8 is literally
`vecSummon.Count`. So vecSummon is the *result* of a summon awaiting collection,
and unopened cubes are now dropped at login and on screen-reopen so a stale one
cannot block every future summon.

### tools/disasm.py

All of the above came from a new tool: `python tools/disasm.py Class.Method`
disassembles any client method with `bl` targets resolved to names through
dump.cs's RVA table and string-table ids annotated inline. `dump.cs` has 103,639
empty method bodies, so "what does the client actually do when..." always ended
in hand-disassembly before this.

### The dashboard

`hc/webui/` -- stdlib `http.server` in a daemon thread beside the asyncio loop,
a JSON API plus one static page, on 127.0.0.1:8099 by default.

`hc/settings.py` is the half that matters to game code: one JSON file, every key
with a default, read *at the point of use* so a change applies on the next
packet with no restart. Wired into gacha rates/cost/duplicates, stage reward
multipliers and per-stage overrides, new-account starting resources and roster,
and equipment consumption.

Saves are editable, exportable and importable as JSON, and an import can bind a
device id in the same step -- which is the actual "move my account to a new
phone" path. Device-to-account mapping already existed in `accounts/index.json`;
the dashboard just surfaces it.

It has no authentication and can rewrite any save, so it refuses a non-loopback
bind unless `dashboard.token` is set. `tools/test_dashboard.py` covers all of
it against a throwaway accounts directory, including that refusal and that
static serving cannot escape `static/`.

### Item names, and hero-specific duplicate shards

The wallet editor showed raw `type1:type2:type3` keys, which is unusable --
`16:41:-1` is Bam's Memory and `5:1403:-1` is a particular sword.
**`ResourceTable`** is the client's own answer: 4,650 rows of
`(ResourceID, Type2, Type3) -> NameID`, covering 148 of the 152 keys in a real
save. `itemList` and `runeList` fill the rest; anything still unknown degrades
to `type N / M` rather than a guess. `TABLES.resource_name()` is the lookup;
the account payload carries names for its own keys, and `/api/resources?q=`
searches the catalogue for the add-an-item picker (4,650 rows is too much to
push to the browser on load).

That exposed a bug in the account summary: **type 0 is Gold, type 4 is User
EXP**, and it had been reporting User EXP as gold.

**SS and SSS duplicates now pay out the hero's own Memory** (ResourceType 16
keyed by unit id) instead of a generic Memory Stone. The amount, 40, is not
invented: `GradeUpMaterial` charges exactly 40 of `16 / <unitID>` to take that
hero from 2 stars to 3, and `GachaUnitPieceRatioView` -- the only other gacha
table paying this resource -- also pays 40. So one SS duplicate is worth one
star-up. `DecompPieceReturn` has no rareness-3 row at all, so before this an
SSS duplicate paid out *nothing*.

The dashboard override (`gacha.duplicate`) accepts the string `"unit"` in the
Type2 slot, meaning "the hero that was actually summoned" -- the only way a
fixed table can express a hero-specific reward.

## Seventh pass: the Arena

Asynchronous PvP, and the first mode that involves other people at all.

### Why classic Arena and not the other three

The protocol has four: Arena, Tag Arena, World Arena and Global Arena. The
last three are real-time and route through the match server or the global match
proxy, so they need two players connected at the same instant -- on a
self-hosted server that is usually nobody. Classic Arena is asynchronous: you
attack a *snapshot* of somebody's defence team and they never have to be
online. That is the one that works for a single player, so that is the one
implemented.

### The client already had 887 opponents

`ArenaAIList` (887 rows: name, rating, EXP, profile) and `ArenaAITeamInfo`
(3,460 rows: the actual line-ups, with level, tier, grade, rarity and gear per
slot) shipped inside `herocantare.db`. 865 of them have a usable team. So a
one-player server gets a full ladder without configuring anything, and the
dashboard's team builder is for *adding* to that rather than a prerequisite.

Everything else is table-driven too:

* `ArenaTierInfo` -- 32 tiers, each with its point threshold, how far
  matchmaking may reach (`MatchRange`), how often it should offer a bot rather
  than a person (`AiMatchRate`), a separate win/lose point swing for every kind
  of opponent (`Ai*`, `Upper*`, `Lower*`), and the per-tier rewards
* `ArenaMatchGroup` -- point band to match group
* `ArenaSeasonReward` -- payout by tier

Losses are stored as positive numbers in the table and subtracted.

### Real players and bots share one pool

`arena.rivals()` scans the saves for any account with units and reads its
defence team; `arena.bots()` returns the shipped teams plus anything built in
the dashboard. `roll_matches` mixes them using the tier's own `AiMatchRate`,
so with one account it is bots all the way down without a special case, and
with several it starts offering people. The save scan is cached for 20 seconds
because it is disk work on a packet path -- but only who is *offered* is
cached; a fight always re-reads the defender's save.

A defence team falls back to the story party, then to the strongest four
heroes. An account that never opens the arena screen is still a valid
opponent, which matters when there are only two people on a server.

Bot account ids are negative, so nothing can confuse one for a real account.

### Worth knowing

* Unlike stages, arena **does** report a loss -- `PlayAreanRewardReq.iWinLose`
  carries it -- so both outcomes are handled. There is still no defeat packet
  anywhere else in the protocol.
* `NGArenaInfo` has three nested structs (`ngCommandCenterInfo`,
  `ngCommandersInfo`, `frameInfo`). They are built empty rather than left null,
  for the usual reason: a null nested struct throws inside the client's own
  marshaller, where the failure is invisible from here.
* `tools/test_arena.py` covers both shapes -- a one-player ladder, and two
  accounts finding and fighting each other -- with a fake session that really
  encodes and decodes every reply, so a malformed DTO fails in the test rather
  than on the phone.

### Slots are positions, and only four of them exist

The single most expensive thing to get wrong. `EUnitPosition` @dump.cs:633232:

```
None = -1   ECommander = 0
EFront_1 = 1   EFront_2 = 2
EBack_1 = 10   EBack_2 = 11   EBack_3 = 12   EBack_4 = 13   EBack_5 = 14
```

**There is no position 3 or 4.** An arena team is four heroes at 1, 2, 10 and
11 -- all 865 usable rows of `ArenaAITeamInfo` use exactly that set, and so
does a real client's own `ChangeArenaDefensePartyReq`.

Getting it wrong fails twice over, and neither failure says anything useful:

* a hero at slot 3 is simply **missing from the opponent preview**, and
* the battle scene then throws `NullReferenceException` inside
  `PlayBaseScene.UnitLoading` (via `PlayUnitLoading` -> `PlayInfoLoading` ->
  `RM_PlayGameInfoLoading` -> `PvPScene.Start`), which on screen is a **black
  screen** -- the game is still running and still takes touches, it just never
  draws. The only trace is in the device's own log:
  `adb logcat -d | grep Unity`.

The matching `SlotType` is `ContentsType.ArenaDefense` = 5 (attack is 6). Send
`SlotType` 0 and the client finds no defence party at all, fields nobody, and
the attacker wins the instant the fight starts.

### Hand-built bot teams are validated, not trusted

The dashboard is the one place a person types numbers that go straight into
that scene, so `arena.check_bot()` repairs a team before it is stored *and*
again when it is read back -- an older `settings.json`, or one edited by hand,
must not be able to black-screen anyone. Every rule is one all 3,460 rows of
`ArenaAITeamInfo` already obey:

| Field | Rule | Where it comes from |
|---|---|---|
| slot | assigned 1, 2, 10, 11 by position; never taken from input | `EUnitPosition` |
| hero | must be `IsPlayerHero` -- 138 of UnitList's 768 rows | every shipped team obeys this |
| team | at most 4, no repeats | 0 of 865 shipped teams repeat a hero |
| level | 1..`TierUpTable.MaxLv` for its tier (30 at T1, 110 at T10) | 0 of 3,460 members exceed it |
| tier | 1..10 | `TierUpTable` |
| grade | 0..6 | `unitGradeStatInfo` carries no other grade |
| rarity | at least the hero's own `Rareness`, at most 3 | no member is ever below its unit's |

Note that the per-unit `MaxGrade` and `NormalTierMax` in `UnitList` are *not*
hard limits -- 1,220 shipped members exceed both, and 1,476 use heroes with no
`unitGradeStatInfo` rows at all. The client tolerates all of that, so the
validator does too. Only the slot is fatal.

## Eighth pass: reading the client for the summon crash

No device time this pass. The summon Ack was chased entirely through
`libil2cpp.so` with `tools/disasm.py`, which turned out to answer more than the
live tests had.

### The handler, confirmed

`JoinDimensionGachaAck` is `NGNetGameServer` @0x14EBE38, and `dump.cs` gives its
signature as

```
bool JoinDimensionGachaAck(int Error, NGCheckServerInfo, NGGachaDaySummonsCount,
                           NGPairInt2, NGIntIntInt, NGGachaChoiceCubeInfo)
```

which is the order `hc/handlers/gacha.py` already sends, so the argument list is
not the problem. It runs, in order: `AddGachaLimitInfo`, then
`CheckServerInfo(_info, ref changeValue)`, then
`RewardDisplayList(changeValue)`, then a walk over the dimension-gacha lists to
work out which summon animation to play, then `_JoinDimensionGachaAck.OnNext`.

The whole thing, including that last call, runs inside `NGNetGameServer`'s
catch. So "the exception is in the Ack" does **not** narrow it to the handler --
every UI subscriber runs inside it too.

### `GachaID` cannot be the trigger

`NMUserInfo.AddGachaLimitInfo` @0x13A3104 does exactly one thing with the value:

```
_dic[item.GachaID] = item        ; a Dictionary indexer *set*
```

It throws only if `item` is null, or the dictionary itself is null -- never
because of what the id happens to be. A set cannot miss. So the older note's
second hypothesis, "-1 versus the banner id", is **dead**, and the `banner` part
below exists only to close it out on the device.

### Where the handler really can throw

Every il2cpp null check in this function ends at `bl 0x1178F00`, the
`NullReferenceException` thunk. The ones that depend on what we send are all in
the dimension-gacha walk, and none of them has a skip path:

| Site | Throws when |
|---|---|
| @0x14EC1E8 | an entry of `vecAddDimensionGacha ++ vecChangeDimensionGacha` is null |
| @0x14EC1EC | `NGDimensionGacha.vecSummon` (+0x20) is null |
| @0x14EC2F4 | any `NGDimensionGachaSummon.ProductInfo` (+0x20) is null |

`NGCheckServerInfo` +0xA0 and +0xA8 are `vecAddDimensionGacha` and
`vecChangeDimensionGacha`, from `dump.cs`. Note this walk is skipped entirely
when both lists are empty -- which is exactly what the workaround does.

Deeper, `NMUserInfo.AddDimensionGacha(NGDimensionGacha)` @0x137CA98 ends in
`_dicDimensionGacha[ngInfo.ID] = ngInfo` and throws if that static
(`NMUserInfo` +0xC0) is null. It is reached once per list entry, so an empty
list cannot trip it and a one-entry list will. That shape -- empty is safe, one
entry is fatal -- matches the symptom better than anything else found.

`tools/test_gacha_ack.py` asserts our own `vecSummon` and `ProductInfo` are
always populated, so the first three sites are ours to keep clean, not ours to
blame.

### The five parts

The safe shape differs from the natural one in five independent ways, not two.
`HC_GACHA_ACK_PARTS` adds them back one at a time:

| Part | What it puts back |
|---|---|
| `wallet` | `vecAddResourceInfo` carries the delta, not the whole wallet |
| `collections` | `vecAddCollectionInfo` carries the counters that changed |
| `units` | `vecAddUnitInfo` announces a genuinely new hero |
| `dimension` | `vecChangeDimensionGacha` carries the banner and its cube |
| `banner` | `NGGachaDaySummonsCount.GachaID` is the banner id, not -1 |

`HC_GACHA_ACK_PARTS=all` is the pre-workaround Ack, and `HC_GACHA_RICH_ACK=1`
still means the same thing. Unset is the safe shape, so the default has not
changed.

Two of the five can be reasoned about without touching a device.
`wallet` and `collections` are both already exercised by the *screen-open* Ack,
which takes the same code path and has never thrown -- so neither is likely.
`units` is empty in practice on a full 138-hero roster, which is what the test
account has. That leaves `dimension` as the one to try first, and it is also the
only part whose absence explains the leftover *"The Dimension has dissipated"*
popup. **Those two open items are probably one bug.**

### Also fixed

`tools/disasm.py` died with a `UnicodeEncodeError` part-way through any dump
containing a Korean or accented string annotation, because a Windows console is
cp1252. It now forces UTF-8 on stdout. The interesting code is usually further
down than the first such string, so this was losing whole dumps.

## Ninth pass: progression gaps, relics, and a fuller bot builder

Five reports off the back of real play. Four were the same shape underneath:
the client had a screen for something the server had never implemented, so the
button did nothing and nothing was logged as wrong.

### "Hero Q has no Memories"

Not a Q problem. `GradeUpMaterial` charges `ResourceType.UnitPieces` (16) keyed
by the hero's own unit id from grade 2 on -- 80, 160, 240, then 320 -- and
**nothing in this server ever paid out type 16**. The only source the real game
had here was an SS-or-better duplicate summon, so any hero pulled once was stuck
at 3 stars forever. New accounts now start with 1,000 of each hero's Memory
(`account.starting_memories`, 0 turns it off). That is +138 wallet rows at
login, taking the payload from 149 resources to 287, which is nowhere near
`MAX_PACKET_SIZE`.

Q's Memory *was* in the dashboard's catalogue all along. The picker was the
problem: it filtered, took the first 200 hits **in table order**, and only then
sorted. Searching `q` matches every name containing a q, so the row wanted was
never in the 200 that survived. It now scores matches -- exact, then
word-start, then substring, shorter names first -- and cuts to the limit
*after* ranking. Searching `q` puts "Q" first and "Q's Memory" second.

### Awakening rewards and rank-up were unhandled packets

Both existed in the spec and had no handler, so the request decoded, logged and
vanished.

* **30101 `UnitAwakenRewardReq`** -> the milestone passives.
  `UnitList.awakenRewardGroup` picks a ladder in `unitAwakenReward`; each rung's
  `conditionValue` is how many nodes must be open (group 1 wants 5/10/15/20,
  group 2 wants 4/8/12/16) and pays Gold. `NGUnitInfo.AwakenRewardGrade` is how
  many rungs are claimed. The handler pays **every** rung earned, not just the
  next one, because players have been awakening heroes while this did nothing.
  Careful with that table: `resourceType1/2/3` is one resource's
  (Type1, Type2, Type3), not three resources.
* **30232 `UnitRarenessUpgradeReq`** -> A / S / SS / SSS.  `rarenessInfo` is the
  whole rule, one row per (unitID, currentRareness): four costs,
  `resultRareness`, and `reqAwakenRoleStat`. That last one points at
  `unitAwakenRoleStat`, whose `openAwakenCnt` is the real gate -- 11 nodes, then
  20, then 29. **It is an awakening gate, not a star gate**, which is why
  starring a hero up never unlocked rank-up.

### Relics were never implemented, and could not have worked

Relics are the client's Artifacts, and they are not gear. Gear stacks in the
wallet as `ResourceType.Item` (5) keyed by itemID; a relic is a per-instance
object with its own UID, living in `NMUserInfo._artifact` and arriving in
`NGLoginAckLargeData.vecArtifactInfo`. The server had no artifact storage, sent
none at login, and handled none of the artifact packets.

The equipping half was broken independently. Relics come through the *same*
`UnitEquipInfoChangeReq` (30007) gear uses, but with `EItemType.ArtifactWeapon`
(**7**) in ItemType and the relic's **UID** in ItemKey -- which is why ItemKey
is a `long`. The old handler read that UID as a stackable item id, looked for it
in the wallet, found nothing and silently refused. So even a player who somehow
had relics could not have worn one.

Now in: `hc/game/artifacts.py`, per-instance storage on the player, a starter
set (`account.starting_relics`), the login payload, equip/unequip/steal through
slot 7, and levelling by feeding relics (30014, worn and locked ones refused,
matching the client's own `Error_MaterialArtifactVaildEquipUnitUID`). Not in:
random stat rolls, unique effects, option enhancement, tier-up, manufacture.

Two data facts worth keeping. Every one of the 216 wearable relics is
`itemType` 7 -- `EItemType` defines ArtifactArmor (8) but this build ships none,
so there is one relic slot. `itemType` 12 is `MaterialArtifact`, the seven rows
at 50001..50007, which are fodder and not wearable.

**`NGUnitInfo.vecArtifactInfo` is the important find.** A unit carries the
relics it is wearing *inline*. That is the only way an opponent's relic can be
drawn at all -- the viewer does not own it and has no other way to learn what it
is. `state.unit_infos(player, units)` attaches them; use it instead of a bare
`unit_info` comprehension anywhere a player's units go to a client, or a hero
shows a relic in `vecEquipInfo` with no relic behind it.

### The arena bot builder

Bots already carried gear (four item ids), which the dashboard never exposed.
They now also carry awakening and a relic, and the builder exposes all of it.

A bot owns nothing, so its relic is **minted per fight** with a throwaway UID
alongside the throwaway unit UID, and travels inside that unit's own
`vecArtifactInfo`. Validation follows the same repair-don't-reject rule as the
rest of `check_bot`: a node id the hero's tree does not contain is dropped, a
material artifact is not wearable, and only one relic fits a slot.

Awakening goes over the wire as `awaken_count` -- "the first N nodes" is how a
player thinks about a tree, and shipping 24,266 `unitAwakenPartsInfo` rows to
the browser to tick boxes would be absurd. Explicit ids still win if given.
Gear is searched through the new `/api/items` endpoint rather than sent whole,
because `itemList` is 2,526 rows against 216 relics.

## Tenth pass: Relics are not Artifacts, and the Forge

Five reports. One of them turned into a naming correction that invalidates part
of the ninth pass's wording, so read this before trusting any sentence with
"relic" in it.

### The client calls two different things by two names, and we had them crossed

| UI says   | protocol and tables say | ResourceType | EItemType slots | forge |
|-----------|-------------------------|--------------|-----------------|-------|
| **Relic** | SceneCard               | 42           | 9, 10, 11       | yes   |
| Artifact  | Artifact                | 8            | 7 (8 unused)    | no    |

The ninth pass implemented **Artifacts** and called them relics throughout. The
code was right, the word was wrong, and the user's reports were always about
Scene Cards. The evidence, none of it inference:

* string 11593 -- *"Relic Craft KIT: A material used in crafting of Relic.
  Relic can be crafted at the Forge."* That names ResourceType **127**, whose
  own display name is "Craft KIT", and 127 is what `sceneCardCreateCost`
  charges. Nothing in the artifact tables costs 127.
* the Forge's classes are all `DimensionHephaiForgeSceneCard*` and its packets
  are `SceneCardManufactureReq` and friends. Strings 11449 "The Forge", 11428
  "Hohoians' Forge", 11426 "Craft Relic!".
* `AwakenRewardRelic.UpdateUI` @0x1BC2078 -- the awakening tree's relic node --
  calls `NMUnit.GetUnitAwakenBonusReward(unitID, partsInfoID)`, and every row
  of `unitAwakenBonusReward` pays ResourceType 42.
* `sceneCardInfo.itemType` is 9 for all 144 cards: `EItemType.SceneCardSlot1`.

Wording is corrected everywhere; behaviour of the artifact code is unchanged.
`account.starting_relics` became `account.starting_artifacts` and the arena bot
field `relics` became `artifacts`, both with the old key read as a fallback so
an existing `settings.json` keeps working.

### Why rank-up to SSS stayed locked

Not the star count, and not the raw node count either. `rarenessInfo`
.`reqAwakenRoleStat` names a `unitAwakenRoleStat` row, and that stat has to be
**claimed** through `AwakenStatOpenReq` (**30228**) -- the Awakening Passive
Mastery slot. 30228 had no handler, so the claim button did nothing and the
stat could never be held, which locked rank-up permanently however many stars
or nodes a hero had. The ninth pass checked `openAwakenCnt` directly, which is
the condition for the stat becoming *claimable*, not the same thing.

Now: 30228 records the claim, `NGLogInAck03.vecAwakenStat` replays it (we sent
that packet empty before, so it would have been forgotten on every relogin
anyway), and rank-up checks the claimed set.

`UnitAwakenStateChangeReq` (**30093**) was also unhandled -- toggling a node on
or off. `unitAwakenPartsInfo.isRecall` says whether a node may be switched off
and `radioBoxGroup` makes siblings mutually exclusive; both are honoured.

### The awakening tree's own Relic

`unitAwakenBonusReward` pays one Scene Card at node **1002** for 32 heroes --
that hero's own Relic. `UnitAwakenReq` never granted it, so the node showed a
reward and handed over nothing. It does now, and the card goes back in
`vecAddSceneCard`.

### The Forge

Four craft slots (`sceneCardCreateSlot`; slot 1 free, 2 and 3 cost gold, 4
costs cash). A craft spends a chosen hero's Memory plus Craft KIT
(`sceneCardCreateCost`, keyed by `CreateLv`, 2/5/8/18/30 pieces), rolls a grade
off that row's own `Grade_1..5_Percent`, and produces one relic:

* `sceneCardCreateUnitBonus` has a row for (hero, rolled grade) -> that exact
  card, every shipped row being `Fixed_Reward = 1`. This is how a hero's own
  relic is crafted.
* otherwise a uniform pick among cards of that `startGrade`, minus the ones
  that only exist as fixed or awakening rewards. 66 of the 108 grade-5 cards
  remain, so the pool is not thin.

Handlers: 30154 slot open, 30155 manufacture, 30156 immediate commit, 30157
collect, plus 30106 level up, 30161 sell, 30183 lock. Every refusal path
matches a named client error (strings 11564..11585, 5281, 5284, 5289).

**One number here is not from the client's data.** No table carries a craft
duration -- not `sceneCardCreateCost`, not `sceneCardCreateSlot`, not any
neighbour -- so it was a server policy value on the real service and it is one
here: `forge.craft_seconds`, default 0, meaning collectable at once. It is
flagged in `hc/game/scenecards.py` rather than buried.

New accounts now also start with 20,000 Craft KIT, or the Forge is unusable
from a standing start.

### The logs

`server/logs/game.log` is from 8 September and predates all of this, so it
could not show the session being asked about. The unhandled packets were found
by diffing the registered handler list against the 859-packet spec instead,
which is exact. If a game server log from a later run turns up, it is worth a
second pass for packets nobody has hit yet.

## Eleventh pass: guilds, exclusive gear, missions, and relics that equip

Confirmed working in game before this pass: Awakening Passive Mastery, rank-up
to SSS, hero Q's star-up, Guild War access, and the 1v1 Arena. Six more reports
came back, and every one had a cause that could be read straight off the
client. No game-server log from these sessions was available -- the user plays
on the phone -- so each was found by diffing handlers against the spec and then
reading the client.

### Relics would not equip: `EquipUnitUID` must be -1, not 0

The relic picker's own filter is `EquipUnitUID == -1`
(`PopupboxSceneCardList.<UpdateList>b__29_6` @0x16FD720 is a single
`cmn x8, #1`). The server sent 0 for an unequipped relic, so every relic read as
already worn and the picker showed none of them. The inventory does not filter,
which is exactly why relics *appeared* but could not be equipped. Artifacts now
send -1 too; their picker does not filter on it, so that half is consistency,
not a confirmed bug. **-1 means "none" throughout this client -- the same trap
as `Type3int64` keys.**

Also read off the client while there: a hero has **two** relic slots, 9 and 10
(`UnitDetailRelicInfo.UpdateUI` @0x1E9D698), not three. They are locked until
awakening node **10002** (slot 9) or **10004** (slot 10) is open --
`itemSlotOpenState`, checked by `NMUnit.CheckItemOpenSlot` @0x149A69C, whose
predicate is `AwakenID == openValue && State == 1`. The server now refuses the
same thing.

### The arena daily reward: the mission claim was a stub

`GetMissionRewardReq` (30029) marked missions done and **paid nothing**, and no
fight advanced any mission counter. The one daily 1v1 arena mission is 104,
"Play 1vs1 Arena 3 times".

`MissionInfo.ClearMission` @0x2278844 switches on ClearType through a jump
table at 0x3C745B4 (indexed ClearType - 1). **`MissionClearType` and
`CollectionType` are different enums and the numbers do not line up**:
mission clear type 12 (ContentsPlayCount) reads **CollectionType 13**, via
`Type3int64.GetKey(13, ClearVal_2)`. Progress is that counter minus the
mission's `StartCollectionValue`.

New `hc/game/missions.py`: arena fights bump ContentsPlayCount and
ContentsClearCount for contents 6; daily and weekly missions go out at login
with a per-period baseline and a received flag; claiming validates, pays from
`missionReward`, and sends `vecChangeMissionInfo`. Using `Season` as the period
index is INFERRED, and only clear types 12 and 13 are validated server-side --
other claims are trusted because the client only enables Claim after its own
check passes. Both are written up in the module.

### Guild War team could not be saved, and trapped the player

`GuildWarsDefensePartyChangeReq` (30250) was unhandled, so the client waited
forever for its Ack (40273) -- no save, and no way off the screen. ContentsType
has no Guild War member, so the team is stored exactly as sent and replayed in
`NGGuildMember.vecGuildWarParty` / `vecGuildWarUnit`.

### Guild buffs stopped upgrading

The buff board takes a buff's level from the guild's `vecBuff`
(`GuildBuffScrollViewItem.Init` @0x19C4D28 -> `GetGuildTopLevelBuff`, falling
back to level 0). The server dropped a buff from `vecBuff` once its timer ran
out, so after six hours the board showed level 0 while the save held the real
level. Unlocked buffs now always go out; an expired one just shows as expired.

### The daily guild donation never reset

The day only rolled over inside the donate handler, but the client greys out
Donate from `NGGuildMember.DonationCount` -- which still carried yesterday's
full count, so the request was never sent and the reset never ran. The day now
rolls wherever the count goes out. `tmLastDonation` also used to be "now"
unconditionally.

### Exclusive equipment could not be upgraded

`ExclusiveLookItemGradeUpReq` (30216) and `EquipExclusiveLookItemGradeUpReq`
(30217) were unhandled. Exclusive gear is `itemList` itemType 15, and
`ExclusiveLookGradeUp` has two modes:

* **Normal / Enhance** -- the click @0x18D6854 runs the same
  `NMUnit.CheckUpgradeItem` @0x1495B78 gear uses: other gear as material
  (`req_itemID` x `req_itemCount`), gold, diamond, and `ResourceType_1` x
  `ResourceVal_1` (some chains cost EventCoin). A per-mille `SuccessRatio`
  roll; failure still spends the materials.
* **Fix / Fusion** -- the click @0x18D776C counts copies of *this* item owned,
  **plus one if it is worn**, against `ItemInfo.FusionCount` (+0xB8), raising
  error 1227 if short. No roll and no gold.

### Tests

New `tools/test_guild_missions.py`, 42 checks. `tools/test_progression.py` is
now 113, with the -1 value and the relic slot lock covered.

## Twelfth pass: mail, with live delivery from the dashboard, and the Guide Mission

### Mail

`PostSendReq`, `GetPostRewardReq`, `GetPostRewardTypeReq` and `DelPostReq`
(30018-30021) were all unhandled, and nothing ever filled the mailbox.

**The real problem was delivery, not the mailbox.** A logged-in account's
`Player` lives in its session's memory and every handler ends in `p.save()`, so
anything the dashboard wrote into that account's save was overwritten on the
session's next save. That is why a dashboard gift needed the game closed and
reopened. New `hc/game/mail.py` never touches a save from the dashboard side:
`send` drops a post into an outbox (memory, mirrored to
`accounts/outbox/<id>.json` for restarts) and the owning session drains it --
at login, and inside `state.resource_sync`, which nearly every Ack goes through.
There is no server-initiated mail packet; posts reach the client only as
`vecAddPost` in reply to something, so delivery is on the player's next action.

Read off the client: `PostType` is System 0 / Event 1 / User 2, with -1 (All)
and -2 (Received) as mailbox filter tabs -- so a claimed post **stays** in the
mailbox marked `ReceivedReward`, and only deleting removes it.
`PostScrollViewItem.Init` @0x163A8EC renders `Subjects` through `int.TryParse`:
an all-digit subject is shown as that client string id, anything else as text,
and unlike `NGServerGroupInfo.ServerName` it cannot throw.

The dashboard's account editor has a "Send mail" card (subject, message,
expiry, searchable attachments, and "send to every account"), backed by
`POST /api/accounts/<id>/mail`, `POST /api/mail` and `GET /api/accounts/<id>/mail`.
The wallet editor now says what was always true: it is only reliable while the
account is offline.

### The Guide Mission

Four chapters, 147 missions, all in the data (`GuideChapterInfo`,
`GuideMissionListInfo`, and `missionList` rows with `MissionType.GuideMission`
= 21). New `hc/game/guide.py` and `GetGuideMissionFinalRewardReq` (30307).

* Chapters go out in `NGLogInAck02.vecUserGuideMissionChapter`;
  `GuideChapterBtn.IsChapterFinalMissionComplete` @0x19C2BC4 is just
  `RewardGained == 1`.
* Every guide mission needs an `NGMissionInfo` -- the chapter screen calls
  `GetMission` for each, and `NMResource.CheckCompleteLastMission` @0x19FFD24,
  the final-reward gate, fails on the first one missing or unclaimed. So the
  final reward needs every mission in the chapter claimed, on both sides.
* **Guide missions are lifetime goals: their baseline is 0.** Daily and weekly
  missions start from the counter's value at the period start; "clear stage
  1-3" must count a stage cleared last month.

**Mission scoring, decoded rather than guessed.** `MissionInfo.ClearMission`
@0x2278844 is a jump table at 0x3C745B4. Each case builds a `Type3int64` key
and falls into one of two shared tails: @0x2279394 compares
`value - start >= ClearVal_1`, @0x22794C0 compares `value - start >= ClearVal_2`.

| ClearType | reads | key | target |
|---|---|---|---|
| 10 DimensionGachaOpenCount | sum of CollectionType 9 | any banner | ClearVal_1 |
| 12 ContentsPlayCount | 13 | ClearVal_2 | ClearVal_1 |
| 13 ContentsClearCount | 14 | ClearVal_2 | ClearVal_1 |
| 15 SceneCardGradeGetCount | 15, or the sum if ClearVal_2 is -1 | grade | ClearVal_1 |
| 29 TargetUnitGradeUpAchievement | 6 | (unit, grade) | > 0 |
| 31 AttendanceCount | 34 | none | ClearVal_1 |
| 34 UnitLevelUp | 37 | unit | ClearVal_2 |
| 45 DungeonClearAchieve | 0 (DungeonClearCount) | dungeon id | -- |
| 46 TotalUnitLevelUp | sum of 37, no baseline | -- | ClearVal_1 |
| 56 AwakenPartsSlotIDMission | 43, or the sum if ClearVal_1 is -1 | node | ClearVal_2 |

64 `UnitEquipAtSpecificSlot` and 65 `UnitSpecificAwaken` read no counter: they
call into `NMUserInfo` and check the hero's own equipment and awakening, which
already go out in `NGUnitInfo`. 61 `MultiCondition_ClearAny` needs
`NGMissionMultiConditionInfo` from the server, which is not sent.

New counters: AttendanceCount (once per UTC day at login), UnitLevelUp (a
hero's best level), GetUnitCount by (hero, grade), AwakenPartsSlotOpenCount,
SceneCardGradeGetCount, and GachaOpenCount per banner. A login backfill rebuilds
them from the save, so earlier progress counts, and never lowers one. The
summon Ack's safe shape still carries no collections -- they are deferred to
the next Ack instead of dropped, so the Ack-parts experiment is unchanged.

INFERRED, and flagged in the code: a multi-pull counts one open per hero; key
43's second part is the node id (the guide rows only use the any-node sum, so
it cannot change a guide result); a Relic counts towards type 15 however it was
obtained; `Season` is 0 for chapters.

**103 of the 147 guide missions can complete.** What blocks the rest is
content this server does not implement: Advent Boss, Dimension Crack, Cube
Dungeon, Hero Dungeon, the Dimension Gap boxes, Heart Heater's Quest House, the
Other World Boss, request quests, equipment summons, accessories, artifact
crafting and upgrade counts, resource spending, total hero level-ups (57), and
multi-condition missions (61). **Because every chapter contains at least one of
those, no chapter's final reward is reachable yet.** Also note that with value
= level for CollectionType 37, "level up any hero 70 times" (46) is met at once
by an account holding all 138 heroes.

One real bug found on the way: mission state built inside the login payload
was created *after* the login save and never persisted. `missions.touch` now
pins it first.

### Tests

New `tools/test_mail_guide.py`, 51 checks, including live delivery from another
thread and the dashboard's HTTP endpoints against a real server.

## Thirteenth pass: modded client files, and a skill builder

Done entirely without the emulator, so everything here stops at "the boot
server serves the right bytes with the right crc". **A phone downloading an
edited bundle and a battle using the new numbers is not verified yet.**

### Getting a phone to fetch a changed bundle

The old note in `download_info` said Unity's bundle `crc` couldn't be
recalculated. It can: **it's a CRC32 over the bundle's uncompressed node data,
concatenated in directory order** (what UnityPy exposes as the nodes of
`BundleFile`). Checked against the shipped values of `script/unit`,
`script/string`, `script/resource`, `script/tutorial` and the two-node
`spritepacker/box` -- and against the device's own error for the patched unit,
`calculated fa601253`, which is exactly what it gives.

That matters because zeroing it had a hidden cost. `NMPatcher.CheckCompareFile`
@0x1F53120 decides whether to download a file with nothing but
`new.crc != old.crc` -- size isn't looked at, and the manifest crc doesn't
short-circuit. Served 0 both times, a phone that already has a bundle never
fetches a changed copy.

So `tools/bootserver.py` now has an **overlay**: `client-overrides/` next to
`server/` (or `--overlay` / `HC_CLIENT_OVERLAY`). A file there is served in
front of the original, and if `client-overrides/crc.json` lists it
(`{"script/unit": 1592191661}`) it goes out with that real crc. Everything else
stays at 0 like before, so no one re-downloads the whole game. crc.json is
re-read on every manifest request and the folder may appear after startup, so
no restart. Deleting the folder is a full revert: crc goes back to 0, which
differs from what the phone has, so it re-fetches the originals.
`tools/test_bootserver_overlay.py`, 26 checks, for both the directory and zip
forms of the client files.

### The skill builder

`server/skill-builder/` (gitignored, has its own README). A stdlib web app:
pick a hero, see each skill level, the special skill and the passives in plain
words, edit them in forms, Apply. It writes `client-overrides/AssetBundle/script/unit`
(SkillData + PassiveEffect, encrypted like the original), `.../script/string`
(English names/descriptions), crc.json, and the same rows into
`table/herocantare.db` (backed up first). "Export for phone" zips the lot with
install/undo commands.

How a kit is put together, read off the tables:

* `unitBaseSkill` = the three skills at level 1; `unitAwakenPartsInfo`
  `unitSkillType` 0 = the next levels (`skillSlotType` 0-2, `skilllevel` 2-5),
  1 = a passive (`skillID` is a PassiveEffect id), 2 = special.
* **Special skills take their name, description and icon from
  `SpecialSkillList`**, not `SkillActiveInfo`; icons are `skill_SpecialSkill_<id>`
  in `spritepacker/common` (actives `skill_ChainSkill_`, passives `skill_PassiveSkill_`).
* **Active and passive ids overlap** (217101 is both), so everything is keyed by kind.
* **Most passives fire a hidden skill.** PassiveEffect effect type 0 ("use an
  active skill") names a SkillData id in `intValue`; 1988 of 2018 exist only as
  `skillType` 1, 29 only as 0. Which one the client picks when both exist is
  UNKNOWN. The stun chance etc. of such a passive lives in that row.
* SkillData ids 1022000-1022002 appear twice with the same type; read-only.
* 170 awakening levels name a skill id that has no SkillData row (mostly
  unreleased units); they're hidden.

What the numbers mean, CONFIRMED against the heroes' own descriptions: an
effect's `EffectPer` is its power (a fraction of ATK for Attack, Poison,
Bleeding, Burn, Heal, piercing attacks; a percent for buffs/debuffs, HP% heals,
shields; a flat number for Speed and Mana), `targetPer` its chance, `turn` its
duration, `count` how many buffs/debuffs a cleanse removes. Passive
`floatValue` is a fraction for every stat except the flat forms of
ATK/HP/DEF/Speed; `If max count effect` + 1 is "(Activated Once)". The
friendlier passive stat names (Debuff resist, Crit resist, Counterattack chance,
...) come from the same check. `EffectPer2`, `CheckAddEffect` and the like are
shown raw -- their meaning varies by effect and isn't pinned down.

Edits only replace the spans of the rows that changed; every other byte of the
tables stays as shipped (the game writes 17-digit floats we couldn't reproduce
anyway). Rebuilt bundles are written uncompressed, so `script/string` grows
from ~8 MB (LZ4HC) to ~30 MB.

`server/skill-builder/tests/test_skill_builder.py`, 53 checks, runs the whole
thing against the real client files in a temp sandbox.

## Fourteenth pass: the rest of the Guide Mission this server can support

Merged first: `ChangeNickNameReq` (30110) from Yukioooh's PR #1.

More of `MissionInfo.ClearMission`'s jump table (0x3C745B4) decoded, for the
guide missions that were still stuck:

| ClearType | reads | target | missions |
|---|---|---|---|
| 3 ItemUpgrade | GetKey(2 ItemGradeUpCount) | ClearVal_1 | 1 |
| 16 ArtifactGradeGetCount | GetKey(16, ClearVal_2), or the sum when -1 | ClearVal_1 | 1 |
| 28 UseResourceType1 | GetKey(26 ResourceUseValue, ClearVal_1) | ClearVal_2 | 4 (content not implemented) |
| 33 CheckCompleteMissionID | `NMMission.CheckCompleteMissionIDClear` @0x1F4DDE8: mission ClearVal_1 is received | -- | 2 (already worked) |
| 57 TotalUnitLevelUpMission | sum of 52 AllUnitLevelUpCount | ClearVal_1 | 4 |
| 61 MultiCondition_ClearAny | see below | -- | 4 |
| 71 ArtifactLvUpCount | `CheckArtifactLVUpCount` @0x1F4C1F0: GetKey(59, ClearVal_1), or GetKey(59) when -1 | ClearVal_2 | 1 (blocked) |
| 72 ArtifactMaxLvUpCount | `CheckArtifactMaxLVUpCount` @0x1F4C418: GetKey(59, grade, 7) | ClearVal_2 | 1 (blocked) |

**MultiCondition (61/62)** was the interesting one. The client needs *two*
things: its own `MissionMultiCondition` row (up to four ClearType + ClearVals)
and an `NGMissionMultiConditionInfo` from the server with a start value per
sub-condition. With no server record the mission is "not done", full stop.
That record goes out in `NGLogInAck02.vecMissionMultiConditionInfo`; the
guide's four are equip/awaken checks on Crusade Mira Yoo (types 64/65), which
read the hero's gear rather than counters, so their start values are 0.

New counters: ItemGradeUpCount (successful equipment upgrades), AllUnitLevelUpCount
(one per hero level), ArtifactGradeGetCount (by grade). The login backfill
rebuilds the last two from the save. INFERRED and flagged: a failed fusion does
not count; one level is one "Level Up Hero"; any artifact obtained counts for
"Craft Artifact", same as relics.

**90 of 147 guide missions can complete now.** The twelfth pass said 103; that
was a miscount -- mission by mission it was 80. The other 57 all need content
that isn't here: Advent Boss (18), Hero Dungeon (10), Trial Tower (7),
resource spending (4: three Trial Tower, one Essence of Dimension), Chaos
Crack (4), Dimension Gap and its boxes (1 + 3),
equipment summons (2), request quests (2), Heart Heater's Quest House (2),
World Raid (1), Other World Boss (1), and real artifact enhancing (2 --
levelling here only adds MaterialEXP and never raises EnchantLevel). Every
chapter still has at least one, so **no chapter's final reward is reachable yet.**

`tools/test_mail_guide.py` now 58 checks.
