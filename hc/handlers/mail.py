"""The mailbox: claiming, claiming everything, deleting, and player-to-player.

    30018 PostSendReq          send a post to another account
    30019 GetPostRewardReq     claim one post
    30020 GetPostRewardTypeReq claim every post of a type (-1 = all)
    30021 DelPostReq           delete posts

All four were unhandled, so the mailbox could show posts but never pay one
out.  Delivery -- including from the dashboard to an online account -- is in
`hc/game/mail.py`.
"""
import logging

from ..net import handler
from ..game import state, mail
from ..game.errors import Err
from ..game.player import Player

log = logging.getLogger('hc.mail')


@handler(30019)
async def get_post_reward(s, a):
    """Claim one post.  It stays in the mailbox, marked received."""
    p = s.player
    want = a['_info']
    post = p.find_post(getattr(want, 'UID', 0))
    if post is None:
        log.info('no post %s to claim', getattr(want, 'UID', 0))
        await s.send(40024, Err.NOT_FOUND, state.resource_sync(p))
        return
    payout = mail.claim(p, post)
    if payout is None:
        log.info('post %s was already claimed', post['uid'])
        await s.send(40024, Err.INVALID, state.resource_sync(p))
        return
    p.save()
    log.info('claimed post %s %r -> %s', post['uid'], post.get('subject'), payout)
    await s.send(40024, Err.OK, state.resource_sync(
        p, vecChangePost=[mail.info(post)]))


@handler(30020)
async def get_post_reward_type(s, a):
    """Claim All.  `_Type` is a PostType, or -1 for every tab."""
    p = s.player
    want = int(a['_Type'])
    changed, total = [], []
    for post in p.posts():
        if want != mail.ALL and int(post.get('type', mail.SYSTEM)) != want:
            continue
        payout = mail.claim(p, post)
        if payout is None:
            continue
        total.extend(payout)
        changed.append(mail.info(post))
    if changed:
        p.save()
    log.info('claimed %d post(s) of type %d -> %s', len(changed), want, total)
    await s.send(40025, Err.OK, want, state.resource_sync(
        p, vecChangePost=changed))


@handler(30021)
async def del_post(s, a):
    """Delete posts.  An unclaimed post's rewards go with it -- the client asks
    before letting a player do that, so the server does not second-guess it."""
    p = s.player
    gone = []
    for want in (a.get('vecDelPost') or []):
        post = p.find_post(getattr(want, 'UID', 0))
        if post is None:
            continue
        p.posts().remove(post)
        gone.append(mail.info(post))
    if gone:
        p.save()
    log.info('deleted %d post(s)', len(gone))
    await s.send(40026, Err.OK, state.resource_sync(p, vecDelPost=gone))


@handler(30018)
async def post_send(s, a):
    """Send a plain post to another account on this server.

    A server hosts several accounts -- one per device -- so this is real, and
    it goes through the same outbox as dashboard mail: an online recipient gets
    it on their next action, an offline one at login.
    """
    p = s.player
    target = int(a['AccountIDTarget'])
    if target == int(p.account_id) or Player.load(target) is None:
        log.info('post to account %s refused: no such account', target)
        await s.send(40023, Err.NOT_FOUND)
        return
    mail.send(target, a.get('Subjects') or '', a.get('Contents') or '',
              sender=p.nickname, sender_id=p.account_id,
              post_type=int(a.get('postType', mail.USER)))
    log.info('%s sent a post to account %d', p.nickname, target)
    await s.send(40023, Err.OK)
