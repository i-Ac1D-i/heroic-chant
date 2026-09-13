"""The Guide Mission: four chapters of missions with a reward at the end of each.

All of it is in the shipped data:

* `GuideChapterInfo`      4 chapters, each with a final reward
                          (`RewardType_1` / `RewardType_2` x `RewardValue`)
* `GuideMissionListInfo`  147 chapter -> mission rows
* `missionList` / `missionReward`  the missions themselves, every one
                          `MissionType.GuideMission` (21)

### Read off the client

* The chapters arrive in `NGLogInAck02.vecUserGuideMissionChapter`, one
  `NGUserGuideMissionChapter(ChapterID, Season, RewardGained)` each.
* `GuideChapterBtn.IsChapterFinalMissionComplete` @0x19C2BC4 is just
  `RewardGained == 1` on that entry.
* The chapter screen (`PopupboxGuideMissionHeroRoad.UpdateMissionItem`
  @0x18E591C) builds each row from `NMUserInfo.GetMission` and
  `NMMission.CheckClearMission`, so every guide mission needs an
  `NGMissionInfo` -- sent by `missions.infos`.
* `NMResource.CheckCompleteLastMission` @0x19FFD24 -- the gate on the final
  reward -- walks the chapter and fails on the first mission that is missing or
  not `IsReceived`.  So a chapter's reward needs **every** mission in it
  claimed, and the server applies the same rule.

Individual guide missions are claimed through the ordinary
`GetMissionRewardReq`; see `hc/game/missions.py` for how each is scored.

INFERRED: `Season` is sent as 0.  The chapters are permanent, not seasonal,
and nothing read so far compares it.
"""
from ..data.tables import TABLES, to_int


def chapters():
    return sorted(TABLES.json('GuideChapterInfo'),
                  key=lambda r: to_int(r.get('ChapterID'), 0))


def chapter(chapter_id):
    return next((r for r in chapters()
                 if to_int(r.get('ChapterID'), -1) == int(chapter_id)), None)


def chapter_mission_ids(chapter_id):
    return [to_int(r['MissionID']) for r in TABLES.json('GuideMissionListInfo')
            if to_int(r.get('ChapterID'), -1) == int(chapter_id)]


def final_reward(chapter_row):
    t1 = to_int(chapter_row.get('RewardType_1'), -1)
    amount = to_int(chapter_row.get('RewardValue'), 0)
    if t1 < 0 or amount <= 0:
        return []
    return [(t1, to_int(chapter_row.get('RewardType_2'), -1), -1, amount)]


def book(player):
    """{"<chapter id>": {"reward_gained": 0 | 1}}"""
    return player.d.setdefault('guide', {})


def reward_gained(player, chapter_id):
    return int(book(player).get(str(int(chapter_id)), {}).get('reward_gained', 0))


def all_claimed(player, chapter_id):
    """Every mission in the chapter received -- CheckCompleteLastMission's rule."""
    from . import missions
    for mid in chapter_mission_ids(chapter_id):
        row = missions.row(mid)
        if row is None or not missions.state(player, row)['received']:
            return False
    return True


def info(player, chapter_id):
    from ..protocol.dto import TYPES
    return TYPES['NGUserGuideMissionChapter'](
        ChapterID=int(chapter_id), Season=0,
        RewardGained=reward_gained(player, chapter_id))


def infos(player):
    return [info(player, to_int(r['ChapterID'])) for r in chapters()]
