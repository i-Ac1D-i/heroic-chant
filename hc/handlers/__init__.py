"""Importing this package registers every C2S handler with hc.net."""
from . import (center, login, dungeon, units, misc, gacha, shop, equipment,  # noqa: F401
               guild, arena, scenecards, mail, commander, achievements,   # noqa: F401
               accessories, hero_dungeon, training_tower, ordeal,   # noqa: F401
               boss)   # noqa: F401
# Last: it only answers requests nothing above handles.
from . import not_open  # noqa: F401,E402
