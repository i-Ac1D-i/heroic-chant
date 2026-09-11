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
unequipping, and levelling one up by feeding it others. What is not: rolling
random stats (`artifactRandStatSelect`), unique effects
(`artifactUniqueCreate`), option enhancement, tier-up and manufacture. An artifact
here carries the flat `stat_Type` / `stat_Effect` its table row gives it.
"""
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


def make(uid, artifact_id):
    """One owned artifact, as it is stored on the player."""
    return {'uid': int(uid), 'id': int(artifact_id), 'exp': 0,
            'enchant': 0, 'unique': -1, 'lock': 0, 'equip': 0}


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


def effects(a):
    """`vecEffectInfo` for one artifact: its table row's flat stat.

    The real game rolls extra random lines on top (`InitRandStatCount` says how
    many). Those are not implemented, so an artifact shows only the stat its row
    defines. The client renders whatever it is sent, so a short list is fine.
    """
    from ..protocol.dto import TYPES
    row = table(a['id']) or {}
    stat = to_int(row.get('stat_Type'), -1)
    if stat < 0:
        return []
    value = float(to_int(row.get('stat_Effect'), 0))
    return [TYPES['NGArtifactEffectInfo'](
        ArtifactUID=int(a['uid']), SlotNum=0, StatTypeID=stat,
        StatEffectValue=value, UseUpgradePoint=0)]


def info(a):
    """NGArtifactInfo for one owned artifact."""
    from ..protocol.dto import TYPES
    return TYPES['NGArtifactInfo'](
        UID=int(a['uid']), ID=int(a['id']),
        MaterialEXP=int(a.get('exp', 0)),
        UniqueRandEffectID=int(a.get('unique', -1)),
        EnchantLevel=int(a.get('enchant', 0)),
        vecEffectInfo=effects(a),
        # This, not the unit's vecEquipInfo, is what the artifact screen reads to
        # decide whether an artifact is worn and by whom.
        EquipUnitUID=int(a.get('equip', 0) or 0),
        LockEnable=int(a.get('lock', 0)),
        ChangeUniqueRandEffectID=-1)


def infos(player):
    return [info(a) for a in player.artifacts()]


# Levelling: feeding artifacts to an artifact.  `artifactEnhanceTable` is the real
# curve; until that is read properly, a fed artifact is worth a flat amount of
# MaterialEXP scaled by its grade, which is monotonic and cannot go backwards.
FEED_EXP_PER_GRADE = 100


def feed_value(a):
    return FEED_EXP_PER_GRADE * max(1, grade_of(a['id']))
