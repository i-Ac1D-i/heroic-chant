"""Profile frames: the picker's list, changing, and releasing.

    python tools/test_frames.py

See state.frame_infos / state.current_frame and handlers/misc.change_frame.
"""
import asyncio
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s  %s%s' % ('PASS' if cond else 'FAIL', name,
                          '' if cond else ('  -- ' + str(detail))))


def main():
    sandbox = tempfile.mkdtemp(prefix='hc-frames-')
    os.environ['HC_SETTINGS'] = os.path.join(sandbox, 'settings.json')
    os.environ['HC_ACCOUNTS_DIR'] = os.path.join(sandbox, 'accounts')
    os.makedirs(os.environ['HC_ACCOUNTS_DIR'])

    from hc.game.player import Player
    from hc.game import state
    from hc.settings import SETTINGS
    import hc.handlers  # noqa: F401
    from hc.handlers import login as login_handler
    from tools.test_progression import FakeSession, call

    try:
        p = Player.create(901100, 'test-device-frames')
        s = FakeSession(p)

        print('\nno frame')
        check('an account that never picked one wears none (-1), not frame 0',
              state.user_info(p).frameInfo.frameID == -1)
        p.d['frame_id'] = 0
        check('an old save\'s frame 0 it does not own also reads as none',
              state.user_info(p).frameInfo.frameID == -1)

        print('\nall frames')
        SETTINGS.set('account.grant_all_frames', False)
        check('with the setting off nothing is granted', p.backfill_frames() == 0)
        SETTINGS.set('account.grant_all_frames', True)
        check('with it on, all 26 frames are granted once',
              p.backfill_frames() == 26 and p.backfill_frames() == 0)
        check('and the picker lists 0..25', sorted(f.frameID for f in state.frame_infos(p))
              == list(range(26)))

        print('\nchanging')
        p.d.pop('frame_id', None)
        d = call(s, 30305, FrameID=3)
        check('an owned frame can be worn, and the Ack says so',
              d['Error'] == 0 and d['_ngUserInfo'].frameInfo.frameID == 3, d)
        check('and is what the profile shows', state.current_frame(p) == 3)
        d = call(s, 30305, FrameID=3)
        check('the same one again is refused', d['Error'] != 0)
        d = call(s, 30305, FrameID=-1)
        check('Release (FrameID -1) takes it off', d['Error'] == 0 and state.current_frame(p) == -1,
              d['Error'])
        del p.d['resources']['170:7:-1']
        d = call(s, 30305, FrameID=7)
        check('a frame not owned is refused', d['Error'] != 0 and state.current_frame(p) == -1)

        print('\nlogin')
        q = Player.create(901101, 'test-device-frames2')
        q.save()
        s2 = FakeSession(q)
        asyncio.run(login_handler.login(s2, {
            'DeviceID': 'test-device-frames2', 'AccountID': q.account_id, 'AuthType': 20,
            'MarketID': 0, 'ClientVersion': 'test', 'statDeviceID': 'x', 'DeviceType': 0,
            'UserLanguage': 0, 'CheckKT': 0, 'ResourceVersion': 0, 'IsReconnect': False,
            '_name': 'LogInReq'}))
        ack01 = next(d for pid, d, *_ in s2.sent if pid == 40000)['ngAck']
        check('a login grants the frames and lists them', len(ack01.vecUserAllFrames) == 26,
              len(ack01.vecUserAllFrames))
    finally:
        os.environ.pop('HC_SETTINGS', None)
        os.environ.pop('HC_ACCOUNTS_DIR', None)
        shutil.rmtree(sandbox, ignore_errors=True)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        print('FAILED: ' + ', '.join(FAIL))
        return 1
    print('ALL CHECKS PASSED (%d)' % len(PASS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
