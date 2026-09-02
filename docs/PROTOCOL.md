# Hero Cantare 1.2.389 — wire protocol

Everything below was recovered by disassembling `lib/arm64-v8a/libil2cpp.so`
(Unity 2020.3.16f1, il2cpp metadata v27) from the shipped APK. RVAs are cited
so each claim can be re-checked.

This supersedes the earlier `server-research/PROTOCOL.md`; see
[Corrections](#corrections-to-the-earlier-notes) at the end for what changed
and why it mattered.

---

## 1. Frame

`NGNet.BasicType`: `HEADSIZE = 13`, `MAX_PACKET_SIZE = 262144`.

| offset | size | field |
|---|---|---|
| 0 | 4 | `packetSize` |
| 4 | 4 | `packetID` |
| 8 | 4 | `sequenceNum` |
| 12 | 1 | `rmiContext` |

`packetSize` is the **whole frame including the 13-byte header**
(`Message.WriteEnd` @0x226F84C sets `Length = position`, and `Message..ctor`
@0x226FAAC starts `position` at 13). The header is always plaintext and never
compressed.

`sequenceNum` is pre-incremented per send, first packet = 1
(`CNetClient.BeginSend` @0x199D734).

**The client never validates the server's sequence numbers.** `SequenceNumRecv`
(field +0xA4) and `SequenceNumFailCount` (+0xA8) are declared but no
instruction in `CNetClient` (0x199CC8C–0x199F600) reads or writes them; only
`SequenceNumSend` (+0xA0) is touched. Incrementing is still worth doing for
fidelity, but nothing depends on it.

## 2. RmiContext (header byte 12)

```
0 Reliable             plain
1 FastEncryp           XOR
2 ReliableCompress     zlib
3 FastEncrypCompress   XOR, then zlib
```

Send order for 3 is **encrypt first, then compress** (`BeginSend`: XOR at
0x199D8D0, then `CompressToMessage`). Receive is the mirror — decompress, then
decrypt (`OnCallbackRecv` @0x199DF38, ctx 3 path at 0x199E18C).

### Compressed frame body

`ZipHelper.CompressToMessage` @0x15D6DCC rewrites the body as:

```
[13:17]  int32   original (uncompressed) body length
[17:  ]  Ionic.Zlib.ZlibStream.CompressBuffer(body)
packetSize = 17 + len(compressed)
```

It is zlib (RFC 1950, `ZlibStream` — not raw deflate, not gzip), so
`zlib.compress` / `zlib.decompress` interoperate directly.

## 3. Encryption — XOR with a server-chosen key

`NGNet.Crypto.XOR_KEY` is a `List<uint>` that the static constructor leaves
**empty**; the server supplies it during the handshake. **For a private server
the key is therefore yours to pick** — no key recovery is needed.

`XOREncrypt` @0x1D4D1E0 (`XORDecrypt` @0x1D4D428 tail-calls it, so it is
symmetric):

```python
def xor(buf, start, total_len, key):       # start is always 13
    if total_len - start < 4: return
    remaining = total_len + 4 - start
    ki, pos = 0, start
    while True:
        v = int32_le(buf, pos)
        k = key[ki] if ki < len(key) else 0
        write_int32_le(buf, pos, k ^ v)
        ki = ki + 1 if ki + 1 < len(key) else 0
        remaining -= 4
        pos += 4
        if remaining <= 7: break
```

The unrolled switch in the binary reads `XOR_KEY[0]`, `[1]`, `[2]` and yields 0
for any higher index, so the key is exactly **three** uint32 words, cycled. A
trailing run of fewer than four bytes is left in the clear.

## 4. Control packets (negative ids)

Dispatched by `CNetClient.OnReceiveServerMessage` @0x199E5B4 via a jump table
indexed by `packetID + 6`.

| id | name | direction | payload |
|---|---|---|---|
| −1 | `HEART_BIT` | both | `int64`; the client echoes it back |
| −2 | `SC_HOSTID_INFO` | S→C | `int64 hostID`, `uint32 key0..2` |
| −3 | `CS_HOSTID_RECV` | C→S | ack |
| −4 | `CS_HOSTID_RECONNECT` | C→S | `int64 hostID` |
| −5 | `SC_RECONNECT_SUCCESS` | S→C | sets ConnectState = 3 |
| −6 | `SC_RECONNECT_FAIL` | S→C | sets ErrorType_ReconnectFail |

`SC_HOSTID_INFO` is the first thing the server sends, in plaintext.

## 5. Body encoding

`NGNet.Message` is a hand-rolled sequential reader/writer — no field tags, no
lengths except where noted. Little-endian throughout (`BitConverter` on a
little-endian target).

| type | encoding | evidence |
|---|---|---|
| `bool` | 1 byte | Marshal.Write @0x2270A08 → `Message.Write(byte)` |
| `byte` | 1 byte | |
| `short` / `ushort` | 2 bytes LE | @0x2270210 |
| `int` / `uint` | 4 bytes LE | @0x2270350 |
| `long` / `ulong` | 8 bytes LE | |
| `float` / `double` | 4 / 8 bytes LE | |
| `string` | **`int32` UTF-8 byte length**, then the bytes | @0x2270710; the encoding getter at 0x3103FF8 is `Encoding.get_UTF8` |
| `DateTime` | **six `int16`**: year, month, day, hour, minute, second | @0x2271D68 |
| `List<T>` | **`int16` count**, then elements | @0x2270F24 |

Two details that will silently corrupt everything if missed:

* the list count is **int16**, not int32;
* a null `List<T>` writes a count of `0`, but a **null nested DTO throws** in
  the client's own marshaller (`cbz x1` → NullReferenceException), so the
  server must always emit a fully-formed nested object.

On read, `DateTime` is validated: year ≥ 1899, month 1–12, day 1–31, hour ≤ 24,
minute ≤ 59, second ≤ 59 (@0x2271C04). Out-of-range values are rejected, so do
not send a zero date.

## 6. Field order is *not* declaration order

Serialisation lives in `UMessageMarshal` (720 generated methods = 180 DTOs ×
{Read, Write} × {scalar, list}). The order and the field set come from those
methods, **not** from the class layout in the dump.

The clearest example: `NGPartyInfo` declares five fields but
`UMessageMarshal.Write(Message, NGPartyInfo)` @0x1410654 writes only four —
`UnitID` (offset 0x24) never goes on the wire.

Non-serialised declared fields found this way:

| type | declared but never written |
|---|---|
| `NGPartyInfo` | `UnitID` |
| `NGGlobalArenaBattleLog` | `iLogIndex` |
| `NGComeBackHeroPassInfo` | `RecvSevenDaysReward` |

The compiler also inlines single-field nested DTO writes (e.g.
`NGLogInAck01.CommandCenterInfo`), which reads as a bare `int` write unless you
track two-level field provenance.

`tools/gen_protocol.py` derives all of this mechanically and reports any packet
whose disassembly disagrees with its declared signature. Currently: **859 / 859
packets agree, 180 / 180 DTOs resolve.**

## 7. RMI services

| namespace | ids | count |
|---|---|---|
| `CenterServerC2S` | 10000–10002 | 3 |
| `CenterServerS2C` | 20000–20003 | 4 |
| `GameServerC2S` | 30000–30363 | 364 |
| `GameServerS2C` | 40000–40386 | 387 |
| `MatchServerC2S` | 50000–50024 | 25 |
| `MatchServerS2C` | 60000–60050 | 51 |
| `GlobalMatchProxyC2S` | 130000–130008 | 9 |
| `GlobalMatchProxyS2C` | 140000–140015 | 16 |

The client ships **both** proxy and stub sides of every service, so the S2C
proxies — which are dead code on the client — are exactly the serialisers a
server must reproduce. That is what makes full extraction possible.

### Session bring-up

```
S→C  -2   SC_HOSTID_INFO(hostID, key0, key1, key2)      plaintext
C→S  -3   CS_HOSTID_RECV
C→S  10000 GetServerGroupInfoReq   → 20000 GetServerGroupInfoAck
C→S  10001 GetConnectGameServerInfoReq → 20001 (HostName, Port)
   [client reconnects to the address it was given]
C→S  30000 LogInReq                → 40000..40004 LogInAck01..05
                                   → 40005 LoginAckLargeData
```

`LoginAckLargeData` carries the bulk state: resources, units, party, posts,
friends, missions, shop goods.

## 8. `NGCheckServerInfo` — the state-delta payload

Nearly every Ack ends with an `NGCheckServerInfo`. It is a set of
`vecAdd*` / `vecChange*` / `vecDel*` lists.

**`vecAddResourceInfo` carries absolute values, not deltas.** Despite the name,
`NMUserInfo.AddResource` @0x138EC88 does `inventory[key] = incoming`
(`Dictionary.set_Item` at 0x138EE8C) and separately computes
`old.GetDifferenceValue(new)` @0x1338EB8 — a plain subtraction — purely to feed
the floating "+N" popup. Sending deltas here would desync every currency in the
game.

Note also that a resource is keyed by the **triple** `(Type1, Type2, Type3)`,
not by `Type1` alone.

## 9. Collection counters -- where progression actually lives

There is no "cleared stages" list anywhere in the 859 packets. The client
answers *"is this stage cleared?"* with `NMUserInfo.CheckClearDungeon`
@0x13A1024, which is literally:

```csharp
GetCollectionValue(Type3int64.GetKey(CollectionType.DungeonClearCount, dungeonID)) > 0
```

So stage progression -- and most other long-term counters -- lives in the
`NGCollectionInfo` list: a `(Type1, Type2, Type3) -> long` map where `Type1` is
a `CollectionType` (`DungeonClearCount = 0`, `NormalDungeonStarCount = 29`,
`HardDungeonStarCount = 30`, ... 61 values in all). Send them in
`NGLoginAckLargeData.vecCollectionInfo` at login and in
`NGCheckServerInfo.vecAddCollectionInfo` on change. Like resources, they are
**absolute**: `NMUserInfo.AddCollection` @0x137FC60 does `dict[key] = incoming`.

### The Type3 = -1 trap

`Type3int64`'s constructor initialises **all three components to −1**
(@0x1CF3EC4: `mov x8, #-1` then stores into +0x10/+0x18/+0x20). The overloads
then fill in only what they are given:

| overload | sets | leaves |
|---|---|---|
| `GetKey(NGCollectionInfo a)` | Type1, Type2, Type3 from the DTO | — |
| `GetKey(long a, long b)` @0x1CF3FEC | Type1, Type2 | **Type3 = −1** |
| `GetKey(long a, long b, long c)` @0x1CF3F64 | all three | — |

`CheckClearDungeon` uses the two-argument form, so the key it looks up is
`(0, dungeonID, -1)` — **not** `(0, dungeonID, 0)`. `Type3int64` derives its
hash and equality from all three fields, so an entry stored with `Type3 = 0`
never matches and every lookup silently returns 0. The visible symptom is
subtle: the game plays perfectly, stages pay out, currencies update — but no
stage ever registers as cleared, so the next one stays locked and first-clear
rewards are offered again forever.

Default `Type3` to −1 for any counter written through the two-argument form.

## 10. Static game data

Contrary to the earlier notes, the tables *are* in the client:

* **`table/herocantare.db`** — 172 SQLite tables: units, skills, items,
  artifacts, runes, scene cards, gacha, guild, and five localisation string
  tables. This is the decrypted form of the `script/unit` AssetBundle, whose
  TextAssets are wrapped in an `adong:` + base64 AES envelope.
* **`files/ngelgames/AssetBundle/script/{dungeon,resource,tutorial,test}`** —
  252 plain-JSON TextAssets, extracted by `tools/extract_assets.py`. Includes
  `dungeonlist` (7 123 stages), `rewardClear` (36 797 reward rows),
  `monsterInfo`, `starSystemReward`, `ShopInfo`, `missionList`, arena AI teams,
  roguelike routes, and the tower/hero-dungeon definitions.

What genuinely is *not* recoverable is the retail server's **logic** — drop-rate
interpretation, matchmaking, and rate limits. See the reward note in
`hc/game/rewards.py` for the one place that required a judgement call.

---

## Corrections to the earlier notes

`server-research/PROTOCOL.md` was a good map, but four of its claims are wrong
or incomplete in ways that would break a server:

1. **"There are no static data tables in the client."** They are in the
   AssetBundles and in `herocantare.db` — around 420 tables in total. The dump
   only showed no `*Table*` *C# types*, because the data is JSON/SQLite, not
   generated classes.
2. **"the server must increment and validate [sequenceNum]"** — the client
   never validates it (§1).
3. It documents the frame and the XOR loop but not the **body encoding**:
   int16 list counts, int32-length UTF-8 strings, and six-int16 DateTimes are
   all load-bearing and none were specified.
4. It says field layouts are "fully known" from declaration order in `dump.cs`.
   They are not — see §6.

The XOR reconstruction in the earlier notes is accurate; this document keeps it
with the loop bound spelled out.
