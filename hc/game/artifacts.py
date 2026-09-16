"""Artifacts.

**Naming, because the client disagrees with itself.**  The English UI calls
ResourceType **42** (`NGSceneCard`) a *Relic* -- that is `hc/game/scenecards.py`
-- and calls ResourceType **8** (`NGArtifactInfo`), which is this file, an
*Artifact*.  An earlier pass here used "relic" for this system throughout; the
wording has been corrected, the behaviour has not changed.

Artifacts are **not** like gear. Gear stacks in the wallet as
``ResourceType.Item`` (5) keyed by itemID; an artifact is a per-instance object
with its own UID, its own rolled stats, and a home of its own -- the client
keeps them in ``NMUserInfo._artifact`` and ships them in
``NGLoginAckLargeData.vecArtifactInfo``, separate from both units and
resources.

Which is why equipping one through the gear path could never work. The client
sends artifacts through the same ``UnitEquipInfoChangeReq`` (30007) that gear uses,
but with ``EItemType.ArtifactWeapon`` (7) in ItemType and the artifact's **UID**
in ItemKey -- and ItemKey is a `long` for exactly that reason. The old handler
read that UID as a stackable item id, looked for it in the wallet, found
nothing and silently refused. See `handlers/equipment.py`.

Two facts off `artifactListTable`, worth not rediscovering:

* every one of the 216 wearable artifacts is ``itemType`` **7**. ``EItemType`` also
  defines ArtifactArmor (8), but this build ships none, so there is one artifact
  slot, not two.
* ``itemType`` **12** is ``MaterialArtifact`` -- the 7 rows at 50001..50007 are
  fodder for levelling, not something a hero wears.

What is implemented: owning artifacts, sending them at login, equipping and
unequipping, levelling one up by feeding it others, and the initial random
substat lines (`InitRandStatCount`) rolled once at creation. What is not:
unique effects (`artifactUniqueCreate`), option enhancement, tier-up and
manufacture.
"""
import random

from ..data.tables import TABLES, to_int
from .enums import ResourceType
from ..settings import SETTINGS

# EItemType, @dump.cs:773764.  These are slots, not resource types.
SLOT_WEAPON = 7                  # EItemType.ArtifactWeapon
SLOT_ARMOR = 8                   # EItemType.ArtifactArmor -- unused in 1.2.389
SLOT_MATERIAL = 12               # EItemType.MaterialArtifact
SLOTS = (SLOT_WEAPON, SLOT_ARMOR)

ARTIFACT = ResourceType.Artifact  # 8, the wallet type for artifact *materials*


def table(artifact_id):
    return TABLES.row('artifactListTable', 'artifactID', artifact_id)


def is_artifact(artifact_id):
    """A wearable artifact, as opposed to levelling fodder."""
    row = table(artifact_id)
    return bool(row) and to_int(row.get('itemType'), -1) in SLOTS


def slot_of(artifact_id):
    row = table(artifact_id)
    return to_int(row.get('itemType'), -1) if row else -1


def grade_of(artifact_id):
    row = table(artifact_id)
    return to_int(row.get('artifactGrade'), 0) if row else 0


def catalogue(grades=None, limit=None):
    """Wearable artifact ids, best grade first.  Used to build the starter set."""
    rows = [r for r in TABLES.sql('artifactListTable')
            if to_int(r.get('itemType'), -1) in SLOTS]
    if grades is not None:
        rows = [r for r in rows if to_int(r.get('artifactGrade'), 0) in grades]
    rows.sort(key=lambda r: (-to_int(r.get('artifactGrade'), 0),
                             to_int(r.get('artifactID'), 0)))
    ids = [to_int(r['artifactID']) for r in rows]
    return ids[:limit] if limit else ids


def _rand_stat_pool(group_id):
    return [r for r in TABLES.sql('artifactRandStatSelect')
            if to_int(r.get('RandStatGroupID'), -1) == int(group_id)]


def _fix_stat_rows(group_id):
    return sorted((r for r in TABLES.sql('artifactFixRandStatGroup')
                   if to_int(r.get('FixRandStatGroup'), -1) == int(group_id)),
                  key=lambda r: to_int(r.get('Index'), 0))


def _rand_stat_value(grade, stat_type):
    for r in TABLES.sql('artifactRandStatAdd'):
        if (to_int(r.get('Grade'), -1) == grade
                and to_int(r.get('Stat_Type'), -1) == stat_type):
            lo_s, hi_s = str(r.get('Base_Min') or 0), str(r.get('Base_Max') or 0)
            lo, hi = float(lo_s), float(hi_s)
            if hi <= lo:
                return lo
            value = random.uniform(lo, hi)
            fractional = '.' in lo_s or '.' in hi_s
            return round(value, 3) if fractional else float(round(value))
    return 0.0


def roll_rand_stats(artifact_id):
    """[(stat_type, value)] for the InitRandStatCount extra lines an artifact
    gets, rolled once at creation."""
    row = table(artifact_id) or {}
    count = to_int(row.get('InitRandStatCount'), 0)
    if count <= 0:
        return []
    fix_group = to_int(row.get('FixRandStatGroup'), -1)
    if fix_group >= 0:
        return [(to_int(r['StatType']), float(r.get('Base_Min') or 0))
                for r in _fix_stat_rows(fix_group)[:count]]
    pool = _rand_stat_pool(to_int(row.get('RandStatGroupID'), -1))
    grade = grade_of(artifact_id)
    picks = []
    for _ in range(min(count, len(pool))):
        weights = [max(0, to_int(r.get('Frequency'), 0)) for r in pool]
        if sum(weights) <= 0:
            break
        pick = random.choices(pool, weights=weights, k=1)[0]
        picks.append(pick)
        pool = [r for r in pool if r is not pick]
    return [(to_int(r['Stat_Type']), _rand_stat_value(grade, to_int(r['Stat_Type'])))
            for r in picks]


def make(uid, artifact_id):
    """One owned artifact, as it is stored on the player."""
    return {'uid': int(uid), 'id': int(artifact_id), 'exp': 0,
            'enchant': 0, 'unique': -1, 'lock': 0, 'equip': 0,
            'rand_stats': roll_rand_stats(artifact_id)}


def starter_set():
    """Artifact ids a new account is given.

    Kept small and deliberately not the best in the game: enough that the artifact
    screen has something in it and equipping can be tested, not a full
    collection.  `account.starting_artifacts` in the dashboard sets how many.
    """
    want = int(SETTINGS.get('account.starting_artifacts',
                            SETTINGS.get('account.starting_relics', 8)))
    if want <= 0:
        return []
    # Grade 3 and 4 rather than 5 -- middling artifacts, with room to level.
    ids = catalogue(grades=(3, 4))
    return ids[:want]


def _enhance_rows(artifact_id):
    grade = grade_of(artifact_id)
    return sorted((r for r in TABLES.sql('artifactEnhanceTable')
                   if to_int(r.get('Grade'), -1) == grade),
                  key=lambda r: to_int(r.get('Enhance'), 0))


def enchant_level(a):
    """Current EnchantLevel (0-7), derived from accumulated MaterialEXP.

    `artifactEnhanceTable.GradeUpEXP` is the incremental cost of the *next*
    step, not a cumulative total, so this walks the steps and spends as it
    goes. `-1` marks the row past the top step.
    """
    exp = int(a.get('exp', 0))
    level, spent = 0, 0
    for r in _enhance_rows(a['id']):
        need = to_int(r.get('GradeUpEXP'), -1)
        if need < 0 or exp - spent < need:
            break
        spent += need
        level = to_int(r['Enhance'], level) + 1
    return level


def effects(a):
    """`vecEffectInfo` for one artifact: its table row's flat stat (plus
    whatever `artifactBaseStatAdd` adds for the current EnchantLevel), then
    its rolled `InitRandStatCount` substat lines, if any.
    """
    from ..protocol.dto import TYPES
    row = table(a['id']) or {}
    out = []
    stat = to_int(row.get('stat_Type'), -1)
    if stat >= 0:
        # stat_Effect is a plain int for some stats and a fraction ("0.063")
        # for others -- to_int() raises ValueError on the latter and falls
        # back to its default, so this was silently zero for every
        # fractional stat.
        value = float(row.get('stat_Effect') or 0)
        level = enchant_level(a)
        if level:
            for r in TABLES.sql('artifactBaseStatAdd'):
                if (to_int(r.get('Grade'), -1) == grade_of(a['id'])
                        and to_int(r.get('Enhance'), -1) == level
                        and to_int(r.get('Stat_Type'), -1) == stat):
                    value += float(r.get('Stat_Effect') or 0)
                    break
        out.append(TYPES['NGArtifactEffectInfo'](
            ArtifactUID=int(a['uid']), SlotNum=0, StatTypeID=stat,
            StatEffectValue=value, UseUpgradePoint=0))
    for slot, (rand_stat, rand_value) in enumerate(a.get('rand_stats') or [], 1):
        out.append(TYPES['NGArtifactEffectInfo'](
            ArtifactUID=int(a['uid']), SlotNum=slot, StatTypeID=rand_stat,
            StatEffectValue=rand_value, UseUpgradePoint=0))
    return out


def info(a):
    """NGArtifactInfo for one owned artifact."""
    from ..protocol.dto import TYPES
    return TYPES['NGArtifactInfo'](
        UID=int(a['uid']), ID=int(a['id']),
        MaterialEXP=int(a.get('exp', 0)),
        UniqueRandEffectID=int(a.get('unique', -1)),
        EnchantLevel=enchant_level(a),
        vecEffectInfo=effects(a),
        # This, not the unit's vecEquipInfo, is what the artifact screen reads to
        # decide whether an artifact is worn and by whom.
        # -1 is the client's "nobody", as it is for Relics -- see
        # scenecards.info.  The artifact picker happens not to filter on it,
        # so this is for consistency rather than a confirmed bug.
        EquipUnitUID=int(a.get('equip', 0) or 0) or -1,
        LockEnable=int(a.get('lock', 0)),
        ChangeUniqueRandEffectID=-1)


def infos(player):
    return [info(a) for a in player.artifacts()]


def feed_value(a):
    """What feeding this artifact to another is worth -- `artifactEnhanceTable`
    materialEXP for its grade and current EnchantLevel, falling back to a
    flat amount scaled by grade if that row is missing."""
    for r in _enhance_rows(a['id']):
        if to_int(r.get('Enhance'), -1) == enchant_level(a):
            return to_int(r.get('materialEXP'), 0)
    return 100 * max(1, grade_of(a['id']))
