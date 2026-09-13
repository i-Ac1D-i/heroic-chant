# Changelog

## Unreleased

### Profile

You can change your nickname, profile frame, background and main hero.
The main hero picker used to be empty no matter how many heroes you had.
Thanks to Yukioooh for these.

### Mail, and gifts that arrive without a relog

The mailbox works: posts show up, can be claimed one at a time or all at once,
and deleted. The dashboard has a **Send mail** card on each account (and a
"send to everyone" option), with attachments. A player who's online gets the
post on their next action, so there's no more closing and reopening the game
after a gift.

### Guide Mission

The Guide Mission is in: all four chapters, their missions, rewards and each
chapter's final reward. **90 of the 147 missions can be completed right now.**
The rest need content this server doesn't have yet (Advent Boss, Hero Dungeon,
Trial Tower, Chaos Crack, Dimension Gap, World Raid and a few more), so no
chapter's final reward is reachable yet.

Mission progress is now tracked the way the game reads it: logins, hero levels
and level-ups, stars, awakening nodes, portal opens, equipment upgrades and so
on. Progress made before this update counts too.

### Guilds

- Guild War teams save, and you can leave that screen without restarting.
- Guild buffs can be upgraded.
- The daily donation resets every day.

### Arena

Claiming the daily reward for arena wins pays out.

### Exclusive equipment

Exclusive equipment can be upgraded.

### For modders

The boot server can serve modified game files from a `client-overrides/`
folder next to `server/`. Files there are sent with their real checksum, so a
phone that already has the originals downloads just the changed ones, and
deleting the folder puts everything back. See `tools/bootserver.py`.

### Tests

```bash
python tools/test_guild_missions.py
python tools/test_mail_guide.py
python tools/test_bootserver_overlay.py
```

## 2026-09-11

### Heroes can be starred up past 3 stars

Star-up charges a hero's own Memory from the third star on, and nothing in the
server ever handed those out — the only source the real game had was pulling a
duplicate of an SS-or-better hero. So most heroes were stuck at 3 stars with no
way forward, whatever else you did.

New accounts now start with 1,000 of every hero's Memory, which is enough to
take one hero from 3 stars to 6. Set `account.starting_memories` to 0 in the
dashboard if you would rather duplicates matter again.

### Awakening rewards can be claimed

The milestone rewards on a hero's awakening tree had no handler at all, so the
Claim button did nothing once you had opened enough nodes. They pay out now,
and a hero who was already past several milestones is paid for all of them at
once rather than one per tap.

### Awakening Passive Mastery can be claimed

Claiming a passive mastery slot also had no handler, so it silently failed.
This was the real reason rank-up was stuck — see below.

Switching individual awakening nodes on and off was likewise unhandled and now
works, including the mutually exclusive ones, where picking a node turns its
sibling off.

### Heroes can be ranked up (A → S → SS → SSS)

Rank-up had no handler either. It also turns out not to be gated on stars at
all: each step needs a specific Awakening Passive Mastery stat to have been
*claimed*, which is why starring a hero up never unlocked anything. With
claiming fixed, the whole chain works, and the claim now survives a relogin
instead of being forgotten.

### The dashboard's resource search actually finds things

The resource picker collected the first 200 matches in table order and only
sorted them afterwards, so anything you searched for with a short query was
usually not among the 200 that survived. Searching `q` could not find "Q's
Memory". Matches are now ranked — exact, then word-start, then anywhere,
shortest first — and the cut happens after ranking.

### The arena team builder sets more of a hero

Bot teams already carried gear, but the dashboard never let you set it. You can
now set each hero's gear in all four slots and how many awakening nodes they
have, alongside the level, tier, stars and rarity that were already there. Gear
is searchable per slot, and awakening is capped at whatever that particular
hero's tree actually holds.

Bad values are repaired rather than rejected, as before: an awakening node a
hero does not have is dropped and explained, instead of reaching the battle
scene and blanking it.

### Summoning: the Ack shape can be tested one field at a time

Summoning still answers with a deliberately thin acknowledgement, because the
fuller one makes the client throw and kills the summon button until restart.
The note in the code claimed the safe shape differed from the natural one in
two ways; it differs in five, which is why the hunt had stalled.

`HC_GACHA_ACK_PARTS` now adds those five back one at a time, so the cause can
be pinned down in a single session at the device instead of a rebuild per
guess. The default is unchanged, so normal play is unaffected.

Reading the client ruled one of the two old suspects out on paper: the summons
counter's banner id cannot be the trigger, because the client does nothing with
it but a dictionary write.

### Tooling

`tools/disasm.py` died partway through any disassembly containing Korean or
accented text, because a Windows console is not UTF-8 by default. Since the
interesting code is usually further down than the first such string, this was
quietly losing whole dumps. Fixed.

Two new test suites, neither needing a device:

```bash
python tools/test_progression.py
python tools/test_gacha_ack.py
```
