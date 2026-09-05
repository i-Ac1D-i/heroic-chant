# Heroic Chant

A private server for a gacha game about manwha.
Get it? *cantare* is "to sing" in italian (funny cause im italian too), so: heroic chant. Close enough.

This is a server the original client talks to, so the game runs again.

It's not a remake or an emulator wrapper. It's the actual 1.2.389 Android
client, unmodified, connecting to Python running on your machine. You log in,
you get your heroes, you play the story, stages pay out, gear works.

## Does it actually work

Yeah. Everything below has been tested against the real client, not just unit
tests:

- login, lobby, the full 138-hero roster
- story stages: turn-based combat, rewards, star flags, unlocking the next one
- progression that survives a relogin
- hero rarity (A / S / SS / SSS)
- level up, star-up (shards + memory stones), the awakening skill tree
- equipment: equip, auto-equip sets, upgrade, sell, runes
- City Search, the idle farm, with a working Claim button
- summoning heroes at the real drop rates
- guilds (founding, donations, buffs, the exchange shop)

Not done: PvP, raids, guild wars. Anything that needs other players, basically.
The shop works server-side but the Store screen only renders its Costume tab.
The gacha grants heroes correctly but the result screen doesn't close itself —
close and reopen and your new hero is there.

**58 of 401 client packets have handlers.** The other 343 decode fine and get
logged with their arguments when they show up, they just don't do anything yet.
That's the main thing to work on if you want to help.

## Running it

```bash
pip install -r requirements-dev.txt   # only needed for the tools
python tools/gen_protocol.py --apk /path/to/herocantare.apk
python tools/extract_assets.py
```

That builds the two things that aren't in the repo: the packet definitions and
the game's data tables. Then:

```bash
python tools/selftest.py     # drives a fake client through the whole stack
python -m hc.main --public-host 192.168.1.50
```

Getting a phone or emulator to actually connect is its own thing —
[docs/TESTING.md](docs/TESTING.md) walks through it. Short version: the client
does an HTTP call before it opens a socket, and `tools/bootserver.py` answers
it and tells the client where your server lives.

If you're on the emulator setup already, `python tools/resume.py` brings
everything back up in one go.

## Configuring it

The server comes with a dashboard, on **http://127.0.0.1:8099** whenever it is
running:

* edit any save -- currencies, items, roster, rank -- with every item shown by
  its real name and a search box to find one, and **export/import** a save as
  JSON to move a player to another device
* summon rates, summon cost, and what a duplicate hero converts to
* stage drop multipliers and per-stage overrides
* what a brand new account starts with
* which device id owns which save

Changes apply on the next packet; nothing restarts. Only what you change is
written, to `server/settings.json`. Delete that file to go back to stock.

Every device that connects gets **its own account**, so several people can play
against one server without sharing a save.

The dashboard has no password and can rewrite any save, so it binds loopback
only; set `dashboard.token` before exposing it anywhere else. `--web-port` moves
it, `--no-web` turns it off.

## Running it on a phone, with no PC at all

There's a second route where the game and the server both live on the phone and
talk over loopback. No emulator, no root, no adb, no DNS.

```bash
python tools/patch_apk.py ../com.ngelgames.herocantare_1.2.389.apk
python tools/make_mobile_bundle.py
```

That produces an APK whose baked-in boot URLs point at `127.0.0.1:8080`, plus a
12 MB data bundle. Install the APK, install Termux, run one setup command, and
that's it — the game downloads its own assets from the server over loopback,
into its own folder, exactly the way it did from the real CDN in 2023. Which is
what removes the root requirement.

Building the APK needs a PC once (Android SDK). Playing doesn't.
[docs/MOBILE.md](docs/MOBILE.md) has the whole thing.

## How it works

The protocol wasn't guessed. Every one of the **859 packets** and **180 wire
structs** came out of disassembling the functions in
`libil2cpp.so`, and each one is cross-checked against its declared signature.
859 out of 859 agree. `tools/gen_protocol.py` regenerates the whole spec from
an APK and reproduces it byte for byte.

[docs/PROTOCOL.md](docs/PROTOCOL.md) has the whole wire format with the
addresses to check every claim against.

## Want to help

Read [CONTRIBUTING.md](CONTRIBUTING.md). It has a worked example of adding a
packet handler start to finish, which is most of what there is to do.

The short pitch: run the server with `--log-level DEBUG`, play, and watch for

```
C2S GameServerC2S.SomeThingReq -- no handler (24 body bytes)
```

That's the todo list, in the order the game actually needs things. The codec
already exists for every one of them, so a handler is usually ten lines.

## Legal

The server is mine and you can do what you want with it.

## Docs

| | |
|---|---|
| [CONTRIBUTING.md](CONTRIBUTING.md) | setup, layout, how to add a feature |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | the wire format, with evidence |
| [docs/TESTING.md](docs/TESTING.md) | getting a device connected |
| [docs/MOBILE.md](docs/MOBILE.md) | phone-only setup, and how the APK patch works |
| [docs/STATUS.md](docs/STATUS.md) | running log of what's been built and what broke |
