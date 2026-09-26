"""Live events the server announces (NGCheckEventInfo.vecEventInfo).

The client gates a lot on events; we serve almost none.  What is here, and
why, read off the client:

EVENT_ID_103 -- the hero summon banners.  ``NMEvent.GetHeroGachaEventInfo``
@0x1F3F9F8 builds the Hero Summon tab's banner list from these (Arg1 gachaID,
Arg2 gachaType, Arg3 eventMarkOnOff, Arg4 name string id, Arg5 banner image id
(< 1 = none), Arg6 a JSON-ish list of description string ids (wrapped in [] by
``NMEvent.StringToList``), Arg7 choiceCubeSeason, Arg8
choiceCubeSummonMaxCount), then appends the Selective Cube from its own table.
And ``NGDimensionGacha.GetGachaEndTime`` @0x1A51FF4 is

    min(tmRenewLastGacha + GachaList.ReSummonMinute, the banner's event tmEnd)

falling back to a date long past when no event names the banner -- which
``DimensionGachaTabHeroUI`` (<Start>b__16) reads as "The Dimension has
dissipated. Moving onto Dimension Select Page." (string 1106) straight after
every pull.  That popup was never about the cube; it was the missing event.

Retail ran these on a calendar; here they are simply always on.
"""
from datetime import datetime, timedelta

from ..protocol.dto import TYPES

GACHA_EVENT = 103          # EEVENT.EVENT_ID_103

# Banners this server runs, and their names (string table): gacha 1 is the
# Time Cube (GachaList: ReSummonMinute 60, DayMaxCount 3), gacha 2 the
# Dimension Cube, the standard hero banner with the pity gauge.
HERO_BANNERS = ((1, 15412), (2, 15411))


def gacha_events(now=None):
    now = now or datetime.utcnow()
    start, end = now - timedelta(days=1), now + timedelta(days=365)
    return [TYPES['NGEventInfo'](
                UID=GACHA_EVENT * 1000 + gid, ID=GACHA_EVENT,
                EventListUID=GACHA_EVENT * 1000 + gid, tmStart=start, tmEnd=end,
                Arg1=str(gid), Arg2='0', Arg3='0', Arg4=str(name_id), Arg5='-1',
                Arg6='', Arg7='0', Arg8='0', Arg9='')
            for gid, name_id in HERO_BANNERS]


def check_event_info(now=None):
    return TYPES['NGCheckEventInfo'](vecEventInfo=gacha_events(now))
