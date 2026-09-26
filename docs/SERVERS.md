# More than one server in the game's server list

The title screen has a server switch. It always showed just one server
("Global"). Now it can list as many as you like: yours, your friends', a list
somebody publishes. Nobody has to ship a new APK for it.

## Adding a server

Open the dashboard (http://127.0.0.1:8099), go to **Servers**, press **Add a
server**, fill in the row and save. Next time the game shows its server list,
the new one is there.

| field | what it is |
|---|---|
| id | any whole number from 2 up (1 is your own server). **Never change or reuse it**: the game remembers which server you picked by this number. |
| name | what the list shows. See "Names" below. |
| address | the other server's address, as the game should dial it: a public IP or a domain name. |
| port | its game port, normally 21010. |
| recommended | the one a brand-new player starts on. Your own server is recommended unless you tick another. |

Untick **List this server** if you only want this install as a list of other
people's servers.

The same thing lives in `settings.json` under `directory`, if you'd rather edit
the file:

```json
{"directory": {"servers": [
  {"id": 2, "name": "Japan", "host": "hc.example.org", "port": 21010}
]}}
```

### Names

The game can only show text from its own string table. The server name is
turned into a number and looked up there; plain text would freeze the login.
So a name has to be something that's already in the game: `Global`, `Japan`,
`Republic of Korea`, a hero's name like `Catherine`, and so on (case doesn't
matter). You can also type a string id straight away. If the text isn't in the
game, the server shows up as its id number instead. The **What players see**
table on the Servers tab shows exactly what each name will look like.

## Sharing a list

Put a JSON file anywhere it can be downloaded (a GitHub gist's raw link works):

```json
{"servers": [
  {"id": 12, "name": "Japan", "host": "hc.example.org", "port": 21010},
  {"id": 13, "name": "Catherine", "host": "203.0.113.7", "port": 21010}
]}
```

Anyone can add its URL under **Shared lists** on the Servers tab. It's
re-downloaded every 15 minutes (`directory.refresh_minutes`), or right away with
**Refresh now**. If the file can't be reached, the servers it listed last time
stay in the list. Pick ids for a shared list that nobody's own servers are
likely to use (12, 13, ... or 100 and up): when two entries have the same id,
the one in your own settings wins and the other is skipped. The Servers tab
says what was skipped and why.

## Running a server other people can join

Other people's games have to reach yours:

* Forward the game port (TCP 21010) on your router to the machine running the
  server.
* Start the server with `--public-host` set to the address people will use
  (`python -m hc.main --public-host your.domain.or.ip`). That's the address
  the game is sent to for the match and PvP connection.
* Give people the address and port, or put them in a shared list.

Nothing else is needed. The first time someone picks your server from their
list, your server makes them an account, the same as for a new player.

## Only list servers you trust

The game logs in with its device id and nothing else, and it sends the same id
to every server it plays on. So whoever runs a server you play on could log in
as you on any other server. Only add servers run by people you trust.

What this setup does to keep that small: building the list never contacts
anybody. A server hears about your device only when you actually pick it. And
a server never lets a device into an account made by a different device.

## How it works

For whoever touches this next. The details, with addresses, are in
`hc/directory.py`.

* The game connects to one "center" server, which is whatever the boot
  server's `real_serverinfo.json` points at: normally your own server. The
  center's `GetServerGroupInfoAck` is the server list. When you pick an
  entry, the game asks the same center for that entry's address
  (`GetConnectGameServerInfoReq`) and connects there directly.
* The game learns its account id **only** from the center. For a server that
  isn't this one, the directory asks that server's own center, speaking the
  same protocol as the game, and passes the answer on. It's cached
  (`accounts/directory_accounts.json`), so the list shows your name and level
  on servers you've used before.
* The regions level of the server switch (the country picker) can't be used
  for this: it only knows four fixed country ids.
* The login no longer trusts the account id the game sends. The device decides
  which account it gets, because with several servers the id a game carries
  may belong to another server.

Tests: `python tools/test_directory.py` (starts a second server of its own).
