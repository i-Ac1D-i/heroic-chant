# Running the whole thing on a phone

No PC, no emulator, no root, no adb, no DNS fiddling. The game and the server
both run on the phone, talking to each other over loopback.

There's one honest caveat up front: **building** the patched APK needs a PC
once, because it uses the Android SDK's `zipalign` and `apksigner`. But that's
a build step, not a play step. Once someone has run it, everyone else just
installs the APK and never touches a computer.

So this doc has two halves: [building](#building-it-once) (done once, on a PC)
and [playing](#playing-phone-only) (done by everyone, on the phone).

---

## How it works

The retail client has two URLs baked into `global-metadata.dat`:

```
http://dlhc.ngelgames.net/herocantare/patchinfo/
http://dlhc.ngelgames.net/herocantare/serverinfo/
```

Both hosts are dead. On a PC setup we redirect them with a hosts file or a DNS
shim, but an unrooted phone allows neither — and even with a redirect, the
client would dial port 80, which an unprivileged Android app cannot bind.

So `tools/patch_apk.py` rewrites the URLs to point at `127.0.0.1:8080`. The
trick that makes it safe is that il2cpp stores each string literal with an
explicit length in a side table, so a different-length replacement would mean
rewriting that table and every offset after it. We dodge all of that by padding
with slashes until the replacement is *byte-for-byte the same length*:

```
http://dlhc.ngelgames.net/herocantare/patchinfo/     48 bytes
http://127.0.0.1:8080/////herocantare/patchinfo/     48 bytes
```

An HTTP server collapses repeated slashes, so the padding is free. Nothing in
the binary moves, which is why this needs no apktool, no smali, and no manifest
surgery — just a byte swap, a repack and a re-sign.

Everything downstream is already ours. The patchinfo JSON *we serve* carries
`serverip`/`serverport` (so the game server address needs no patch at all) and
`cdninfo` (so the client downloads its own AssetBundles from us, into its own
private directory, exactly as it did from the real CDN in 2023). That last part
is what removes the root requirement: nothing ever reaches into `/data/data`
by hand. (The client actually writes to
`/storage/emulated/0/Android/data/com.ngelgames.herocantare/files/ngelgames/`
— app-specific external storage, which needs no permission at all.)

One wrinkle worth knowing: the `crc` on each manifest entry is *not* a CRC32 of
the file on disk. Unity computes it over the decompressed bundle internally, so
it cannot be recalculated for a bundle that has been modified — and
`tools/patch_units.py` exists to modify one. Serving a patched `script/unit`
against the shipped manifest gets a CRC mismatch, then a NullReferenceException
in `NMUnit.TextDecrypt`, which on screen looks like the download wedging at
"CollectionBook 85/227". Unity skips the check when the crc is 0, so the boot
server zeroes them all — the bytes come off local disk over loopback, so there
is nothing to guard against anyway.

---

## Building it once

On a PC, with the tree set up as in [CONTRIBUTING.md](../CONTRIBUTING.md) and
`gen_protocol.py` / `extract_assets.py` already run.

You need the Android SDK build-tools (`zipalign`, `apksigner`) and a JDK. If
you've ever installed Android Studio you have both.

**1. Patch the APK.**

```bash
python tools/patch_apk.py ../com.ngelgames.herocantare_1.2.389.apk
```

Generates a signing key on first run (`tools/heroicchant.keystore` — gitignored,
keep it if you ever want to ship an update the phone will accept as the same
app), and writes `com.ngelgames.herocantare_1.2.389-heroicchant.apk`.

**2. Build the data bundle.**

```bash
python tools/make_mobile_bundle.py --no-db
```

Writes `heroic-chant-data.zip` (~2 MB): `spec.json` and the 252 JSON tables.
The phone can't generate these itself — `gen_protocol.py` needs capstone and a
33 MB `dump.cs`, `extract_assets.py` needs UnityPy, and neither installs
cleanly under Termux on arm64. Drop `--no-db` to fold `herocantare.db` in too
(~12 MB) if you would rather host one file than two.

**3. Zip the client files.**

```bash
cd .. && zip -r ngelgames.zip files/ngelgames
```

~2 GB. This is what the server feeds back to the game as its CDN.

**4. Host the three artifacts** — the patched APK, `heroic-chant-data.zip` and
`ngelgames.zip` — anywhere with direct downloads, then put their ids or URLs at
the top of `tools/termux-setup.sh` (or let people pass `HC_APK_URL`,
`HC_DATA_URL`, `HC_FILES_URL`). After that, players run one command and never
see any of this.

> The APK you host **must be the patched one**. An unpatched retail APK still
> points at `dlhc.ngelgames.net`, which is dead and which an unrooted phone
> cannot redirect — it installs fine and then hangs on the loading bar with
> nothing in any log. `termux-setup.sh` checks before installing and refuses if
> it isn't patched; you can check by hand with
> `python tools/patch_apk.py --check <apk>`.

---

## Playing (phone only)

Install **Termux from F-Droid** — https://f-droid.org/packages/com.termux/ —
not the Play Store build, which is abandoned and whose package manager no
longer works.

Then open it and paste one command:

```bash
pkg install -y curl && curl -sL https://raw.githubusercontent.com/i-Ac1D-i/heroic-chant/main/tools/termux-setup.sh | bash
```

That's the whole install. It fetches the server, the ~2 GB of client files, the
data tables and the game itself, then installs the game if you don't already
have it (you tap Install once, on Android's own dialog) and runs a self-test.

Anything already sitting in your Downloads folder gets used instead of being
re-downloaded, and any download that dies partway resumes on the next run.

Then:

```bash
~/heroic-chant/start.sh
```

Switch to Hero Cantare and play. First launch shows **"New patch is available.
Download now? — 862 MB"**; tap OK. That's the game pulling its assets from the
server over loopback — nothing leaves the phone, and it only happens once.

### Updating

Re-run the same command:

```bash
curl -sL https://raw.githubusercontent.com/i-Ac1D-i/heroic-chant/main/tools/termux-setup.sh | bash
```

It checks the repo for new commits, lists what changed, and fast-forwards. It
skips every download it already has, so this is quick.

---

## Keeping it alive

Android aggressively kills background apps, and if it kills Termux the game
loses its server mid-session.

Before starting, run:

```bash
termux-wake-lock
```

And exempt Termux from battery optimisation in Android's settings
(Settings → Apps → Termux → Battery → Unrestricted). Without this you'll get
random disconnects that look like server bugs.

---

## When it doesn't work

| What you see | What's wrong |
|---|---|
| "App not installed" | The retail version is still installed. Uninstall it first — different signing key |
| Game hangs on the loading bar | The server isn't running, or Termux got killed. Check `~/heroic-chant/logs/boot.log` |
| `boot.log` empty, game stuck | The APK wasn't patched. A retail APK still points at the dead CDN — check you installed the `-heroicchant` one |
| Setup script: "Can't see your Downloads folder" | Run `termux-setup-storage` and tap Allow, then re-run |
| Setup script: "ngelgames.zip not found" | It has to be in Downloads and its name has to start with `ngelgames` |
| Asset download stalls partway | Usually free space. It needs ~2 GB beyond the zip |
| Stuck at "CollectionBook 85/227" | A bundle failed its CRC check, so the unit tables never loaded and `NMUnit.TextDecrypt` threw. The boot server clears the manifest CRCs to prevent this — check `boot.log` says "crc cleared" |
| Nothing downloads, client just sits there | It's waiting on the "New patch is available. Download now?" dialog. Tap OK |
| `pkg install` fails | You're on the Play Store Termux. Uninstall it, get the F-Droid build |
| Game connects then drops after a few minutes | Android killed Termux. `termux-wake-lock` and disable battery optimisation |

To see what the client is actually asking for:

```bash
tail -f ~/heroic-chant/logs/boot.log
tail -f ~/heroic-chant/logs/game.log
```

---

## Pointing a phone at a PC instead

If you'd rather run the server on a PC and just use the patched APK on the
phone, build it with your LAN IP instead of loopback:

```bash
python tools/patch_apk.py in.apk --host 192.168.1.50 --port 8080
```

The host and port have to fit in the same byte count as the original URL, which
gives you 48 characters to play with — plenty for any LAN address, but the tool
will tell you if you somehow overflow it. Then run the server as in
[TESTING.md](TESTING.md), with `--http-port 8080`, and skip all the DNS and
hosts-file steps: the APK already knows where to look.
