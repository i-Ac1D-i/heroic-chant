"""Error codes for the `Error` field almost every Ack starts with.

The client has its own 453-value error enum (`NMError` in game/enums.py) with
entries like `Error_CheckGradeUpResource1 = 1011`, and it maps some of those to
specific on-screen messages. We mostly don't use them: for a private server the
useful distinction is "did it work", and anything non-zero makes the client
refuse the action, which is the behaviour we want.

So these are *our* convention, not the client's. Zero is the only value the
client treats specially. If you're implementing something where the player
deserves a real message ("not enough Fire Shards" rather than a silent no-op),
reach for `NMError` instead -- `NMError.name(code)` will tell you what a value
means, and grepping dump.cs for the name shows where the client uses it.
"""


class Err(object):
    OK = 0            # it worked
    NOT_FOUND = 1     # no such unit / guild / listing
    NOT_ENOUGH = 2    # can't afford it
    INVALID = 3       # nothing to do: maxed out, no upgrade path, already owned
    LOCKED = 4        # gated behind level, rank, or a clear you don't have
