"""The Advent Boss: StartBossDungeonReq (30179), EndBossDungeonReq (30180) and
EndNewBossDungeonReq (30344).

See hc/game/boss.py for the rules as the client has them.
"""
import logging

from ..net import handler
from ..game import state, rewards, boss
from ..game.errors import Err

log = logging.getLogger('hc.boss')


@handler(30179)
async def boss_start(s, a):
    p, did = s.player, int(a['_DungeonID'])
    party = a['_vecPartyInfo'] or []
    err = boss.can_start(p, did)
    if err:
        log.info('advent boss %d refused: %d', did, err)
        await s.send(40195, err, did, party, state.resource_sync(p))
        return
    boss.charge(p, did)
    if party:
        p.set_party(party[0].SlotType,
                    [{'slot_index': u.SlotIndex, 'unit_uid': u.UnitUID,
                      'skill_on_off': u.SkillOnOff} for u in party])
    p.d['boss_active'] = did
    p.save()
    log.info('advent boss %d start', did)
    await s.send(40195, Err.OK, did, party, state.resource_sync(p))


async def _end(s, a):
    """A win pays the floor's drops, and its daily bonus while today's three
    last; a loss pays nothing (the stamina went at the start)."""
    p, did = s.player, int(a['_DungeonID'])
    active = p.d.pop('boss_active', None)
    won = int(a['_ClearType']) == boss.WIN
    daily, extra = [], {}
    before = len(p.d['units'])
    if active != did:
        log.info('advent boss end for %d with no battle started -- nothing paid', did)
    elif won:
        first = boss.record_win(p, did)
        drops = rewards.roll(did, first_clear=first)
        rewards.grant(p, drops)
        daily = boss.take_daily(p, did)
        rewards.grant(p, daily)
        if daily:
            extra['vecChangeBossDungeonDailyReward'] = [boss.daily_info(p, boss.DAILY_GROUP)]
        log.info('advent boss %d cleared (first=%s): %d drops, daily %s',
                 did, first, len(drops), daily)
    else:
        log.info('advent boss %d lost', did)
    p.save()
    new_units = [state.unit_info(u) for u in p.d['units'][before:]]
    await s.send(40196, Err.OK, did,
                 state.resource_sync(p, vecAddUnitInfo=new_units, **extra),
                 [state.resource_info(*r) for r in daily], 0)


@handler(30180)
async def boss_end(s, a):
    await _end(s, a)


@handler(30344)
async def new_boss_end(s, a):
    await _end(s, a)
