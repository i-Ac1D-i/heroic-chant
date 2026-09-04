# Getting the game running

## 0. Check the server first (no device needed, 5 seconds)

```bash
cd server
python tools/selftest.py
```

This drives a mock client through the real server: handshake, center server,
login, a stage clear with reward payout, a level-up. If this fails, fix that
before touching a device.

---

## 1. What you need

**An ARM-capable Android.** The APK ships **arm64-v8a and armeabi-v7a only**
— no x86. A stock Android Studio AVD on an x86_64 image will not run it. Use
**LDPlayer 9** or **MuMu Player 12** (both do ARM translation), or a real phone.

**Root is not required.** It used to say otherwise here, on the assumption that
the AssetBundles had to be placed inside `/data/data/`. They don't: the client
keeps them in `/storage/emulated/0/Android/data/com.ngelgames.herocantare/files/ngelgames/`,
which is app-specific *external* storage — writable over adb with no root and
no SELinux relabelling. And you usually don't need to place them at all, because
`bootserver.py` stands in for the dead CDN and the game downloads its own copy
(see §2).

Emulator settings: Android 9 or 11, 4 GB RAM, and enough disk for ~4 GB of app
data. Root is still handy for poking around, just not needed.

---

## 2. Install the client and restore its data

```bash
adb install ../com.ngelgames.herocantare_1.2.389.apk
```

Launch it once so Android creates the data directory, then force-stop it.

**You probably don't need to push anything.** `bootserver.py` serves the asset
CDN, and the patchinfo it hands the client points `cdninfo` back at it, so on
first launch the game downloads its own AssetBundles exactly as it did in 2023.
Confirmed end to end: 1,913 requests, ~1.95 GB, no root involved. Just start the
servers (§6) and launch the game.

If you would rather seed them by hand — to skip the download, or because you
already have the dump — they go in external app-specific storage, which adb
can write directly:

```bash
adb push ../files/ngelgames /sdcard/Android/data/com.ngelgames.herocantare/files/ngelgames
```

No `su`, no `chown`, no `restorecon`. The old instructions here copied into
`/data/data/` and needed all three; that was simply the wrong directory.

---

## 3. Find your LAN IP

```bash
ipconfig
```

Take the IPv4 address of the adapter your device shares a network with (e.g.
`192.168.1.50`). Use it everywhere below. **Not `127.0.0.1`** — that means the
device itself.

---

## 4. Point the client at your machine

The client can reach a server address by two independent routes, so set up
both. Whichever it takes, it lands on you.

### a) Rewrite the config already on the device

```bash
python tools/patch_client_config.py ../files/ngelgames --host 192.168.1.50
```

Then push the patched directory back (step 2 again). This covers the client's
own baked-in hostname→IP table, which it prefers over the system resolver.

### b) Serve the boot endpoints

Before opening any TCP socket the client fetches two files over plain HTTP:

```
http://dlhc.ngelgames.net/herocantare/patchinfo/real_patchinfo12389.json
http://dlhc.ngelgames.net/herocantare/serverinfo/real_serverinfo.json
```

`tools/bootserver.py` serves both with your address substituted, and answers
DNS for the ngelgames domains:

```bash
# Administrator terminal -- binds ports 53 and 80
python tools/bootserver.py --host 192.168.1.50
```

Then make the device resolve those names to you:

* **Emulator (root):**
  ```bash
  adb shell su -c "mount -o rw,remount /system"
  adb shell su -c "echo '192.168.1.50 dlhc.ngelgames.net' >> /system/etc/hosts"
  adb shell su -c "echo '192.168.1.50 hcus01.ngelgames.net' >> /system/etc/hosts"
  ```
* **Real phone, no root needed for this part:** Wi-Fi → your network → modify →
  Advanced → IP settings **Static** → set DNS 1 to `192.168.1.50`. The
  bootserver's DNS answers ngelgames names itself and forwards everything else
  to `8.8.8.8`, so normal browsing keeps working.

---

## 5. Open the firewall

The usual reason nothing connects. In an Administrator terminal:

```bash
netsh advfirewall firewall add rule name="HeroCantare" dir=in action=allow protocol=TCP localport=21010,80
```

```bash
netsh advfirewall firewall add rule name="HeroCantareDNS" dir=in action=allow protocol=UDP localport=53
```

---

## 6. Run it

Two terminals:

```bash
python tools/bootserver.py --host 192.168.1.50
```

```bash
python -m hc.main --public-host 192.168.1.50 --log-level DEBUG
```

Before launching the game, confirm from the device's browser that
`http://dlhc.ngelgames.net/herocantare/serverinfo/real_serverinfo.json` returns
JSON containing your IP. If that works, the hard part is done.

---

## 7. Launch the game

A healthy boot looks like this in the game-server log:

```
hc.net    connected ('192.168.1.77', 41xxx)
hc.net    handshake complete with (...) (hostID 1)
hc.net    C2S CenterServerC2S.GetServerGroupInfoReq
hc.center new account 1001 for device <id>
hc.net    C2S CenterServerC2S.GetConnectGameServerInfoReq
hc.net    connected (...)                     <- reconnects to the game server
hc.net    C2S GameServerC2S.LogInReq
hc.login  login: account 1001 (Player1001) client 1.2.389, 138 units
```

Then play: the roster is already full, story stages run, and clears pay out.

Anything unimplemented shows as

```
hc.net    C2S GameServerC2S.<Name> -- no handler (N body bytes)
```

which is exactly the list of what to write next.

---

## 8. When it goes wrong

| symptom | cause |
|---|---|
| Game hangs on the loading/patch screen | it never reached the boot HTTP shim — check the device browser test in §6, and the firewall |
| `connected` then immediate disconnect, no `handshake complete` | the client didn't like the handshake; run with `--log-level DEBUG` and check for a decode error |
| Nothing at all in either log | DNS is not redirected. Confirm with `adb shell ping dlhc.ngelgames.net` — it must show your IP |
| App crashes at splash | ARM translation missing (x86 emulator), or the asset download never finished — check `bootserver.py`'s log for what it last served |
| Stuck after login, black screen | the client is waiting on a packet with no handler; the `no handler` lines name it |
| `cannot bind port 80` | not running as Administrator, or IIS/Skype holds port 80 |

Accounts live in `server/accounts/<id>.json` as plain JSON — edit resources or
units there and restart the client to pick up the change. Delete the file (and
the device id entry in `accounts/index.json`) to start fresh.

## A note on cleartext HTTP

The APK targets SDK 33 and does **not** set `usesCleartextTraffic` or reference
the `network_security_config.xml` it ships, which would normally block plain
HTTP. It works anyway because Unity's `UnityWebRequest` uses its own native
stack rather than Android's — and empirically, the retail game ran this way.
If you ever repackage the APK and HTTP stops working, add
`android:usesCleartextTraffic="true"` to the `<application>` tag.
