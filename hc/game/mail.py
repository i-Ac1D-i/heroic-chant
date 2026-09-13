"""Mail: the in-game mailbox, and a way for the dashboard to fill it live.

### Why mail needs an outbox, not a save edit

The dashboard runs in a thread beside the game loop.  A logged-in account's
`Player` is held in memory by its session, and every handler ends in
`p.save()` -- so anything the dashboard writes straight into that account's
save file is overwritten by the session's next save.  That is the whole reason
a gift from the dashboard used to need the game closed and reopened.

So nothing here touches a save from the dashboard side.  `send` drops the post
into an **outbox** -- in memory, and mirrored to `accounts/outbox/<id>.json`
so a restart does not lose it -- and the owning session **drains** it:

* at login, before the mailbox is sent, and
* inside `state.resource_sync`, which nearly every Ack goes through.  So a post
  sent from the dashboard lands in the mailbox on the player's next action --
  opening a menu, finishing a fight, levelling a hero -- with no reconnect.

There is no server-initiated mail packet in the protocol: new posts reach the
client in `NGCheckServerInfo.vecAddPost`, and only in reply to something.

### Read off the client

* `PostType` @dump.cs:661682 -- System 0, Event 1, User 2.  -1 (All) and -2
  (Received) are the mailbox's filter tabs, not post types.  A claimed post
  therefore **stays** in the mailbox with `ReceivedReward` set, which is what
  the Received tab shows; only deleting removes it.
* `NMUserInfo.CheckServerInfo` applies vecDelPost, then vecAddPost, then
  vecChangePost -- the last two both through `AddPost`, keyed by UID.
* `PostScrollViewItem.Init` @0x163A8EC renders `Subjects` through
  `int.TryParse`: a subject that is all digits is shown as that client string
  id, anything else as plain text.  `TryParse`, so unlike
  `NGServerGroupInfo.ServerName` a text subject cannot throw.
"""
import json
import os
import threading
from datetime import datetime, timedelta

SYSTEM, EVENT, USER = 0, 1, 2          # PostType
ALL = -1                               # the "All" filter tab
DEFAULT_REMOVE_HOURS = 24 * 30
SENDER = 'Heroic Chant'

_LOCK = threading.Lock()
_PENDING = {}                          # account id -> [post, ...]


def _outbox_dir():
    from .player import ACCOUNTS_DIR
    return os.path.join(ACCOUNTS_DIR, 'outbox')


def _outbox_path(account_id):
    return os.path.join(_outbox_dir(), '%d.json' % int(account_id))


def _write(account_id, posts):
    path = _outbox_path(account_id)
    if not posts:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(_outbox_dir(), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(posts, fh, indent=1)
    os.replace(tmp, path)


def _read(account_id):
    try:
        with open(_outbox_path(account_id), encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, ValueError):
        return []


def make(subject, contents='', rewards=(), sender=SENDER, sender_id=0,
         post_type=SYSTEM, remove_hours=DEFAULT_REMOVE_HOURS, now=None):
    """A post as it waits in the outbox.  No UID yet -- the owner assigns it."""
    clean = []
    for t1, t2, t3, amount in rewards or ():      # always (t1, t2, t3, amount)
        if int(amount) > 0 and int(t1) >= 0:
            clean.append([int(t1), int(t2), int(t3), int(amount)])
    return {
        'type': int(post_type), 'from_id': int(sender_id),
        'from_name': str(sender or SENDER),
        'subject': str(subject or ''), 'contents': str(contents or ''),
        'reg': (now or datetime.utcnow()).isoformat(timespec='seconds'),
        'remove_hours': max(1, int(remove_hours)),
        'received': False, 'rewards': clean,
    }


def send(account_id, subject, contents='', rewards=(), **kw):
    """Queue a post for an account.  Safe from any thread.  Returns the post."""
    post = make(subject, contents, rewards, **kw)
    with _LOCK:
        queue = _PENDING.setdefault(int(account_id), _read(account_id))
        queue.append(post)
        _write(account_id, queue)
    return post


def pending(account_id):
    """How many posts are waiting to be picked up."""
    with _LOCK:
        if int(account_id) in _PENDING:
            return len(_PENDING[int(account_id)])
        return len(_read(account_id))


def drain(player):
    """Move this account's waiting posts into its mailbox.  Returns the new ones.

    The player is saved *before* the outbox file is removed, so a crash in
    between delivers a post twice rather than losing it.
    """
    aid = int(player.account_id)
    with _LOCK:
        queue = _PENDING.pop(aid, None)
        if queue is None:
            queue = _read(aid)
        if not queue:
            return []
        box = player.posts()
        new = []
        for post in queue:
            post = dict(post, uid=player.new_uid())
            box.append(post)
            new.append(post)
        player.save()
        _write(aid, [])
    return new


def has_pending(account_id):
    """Cheap check for the hot path: only memory, never disk."""
    return bool(_PENDING.get(int(account_id)))


def load_pending(account_id):
    """Pull a file-only outbox into memory -- done once per login."""
    with _LOCK:
        if int(account_id) not in _PENDING:
            queue = _read(account_id)
            if queue:
                _PENDING[int(account_id)] = queue


# --------------------------------------------------------------- mailbox --
def expired(post, now=None):
    try:
        reg = datetime.fromisoformat(post['reg'])
    except (KeyError, TypeError, ValueError):
        return False
    return (now or datetime.utcnow()) >= reg + timedelta(
        hours=int(post.get('remove_hours', DEFAULT_REMOVE_HOURS)))


def prune(player, now=None):
    """Drop expired posts.  Returns how many went."""
    box = player.posts()
    keep = [p for p in box if not expired(p, now)]
    gone = len(box) - len(keep)
    if gone:
        box[:] = keep
    return gone


def info(post):
    from ..protocol.dto import TYPES
    from . import state
    return TYPES['NGPostInfo'](
        UID=int(post['uid']), Type=int(post.get('type', SYSTEM)),
        FromAccountID=int(post.get('from_id', 0)),
        FromNickname=str(post.get('from_name', SENDER)),
        Subjects=str(post.get('subject', '')),
        Contents=str(post.get('contents', '')),
        RegDate=state._dt(post.get('reg')),
        RemoveHour=int(post.get('remove_hours', DEFAULT_REMOVE_HOURS)),
        ReceivedReward=bool(post.get('received')),
        vecRewardInfo=[state.resource_info(t1, t2, t3, amount)
                       for t1, t2, t3, amount in (post.get('rewards') or [])])


def infos(player):
    return [info(p) for p in player.posts()]


def claim(player, post):
    """Pay a post out.  Returns the rewards granted, or None if nothing to take."""
    from . import rewards
    if post.get('received'):
        return None
    payout = [tuple(r) for r in (post.get('rewards') or [])]
    rewards.grant(player, payout)
    post['received'] = True
    return payout
