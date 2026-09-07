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

What works is answering a summon with the same Ack *shape* as a screen-open:
the whole wallet instead of a delta, and `NGGachaDaySummonsCount.GachaID = -1`.
Those two things have never been varied independently, so that is exactly where
the next attempt should start. `HC_GACHA_RICH_ACK=1` restores the fuller Ack for
whoever picks it up. Remaining cost: a genuinely *new* hero is not announced, so
it only shows after a relogin. Duplicates are unaffected.

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
