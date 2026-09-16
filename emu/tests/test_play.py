"""Play cockpit without a core or a browser: WebSocket framing, input bookkeeping, pacing.

The live path (core, memory, absolute mouse against the real engine) is checked by
emu/play_live_test.py; these run in milliseconds and pin the logic that path depends on.
"""
import io
import pathlib
import struct
import sys
import unittest

EMU = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EMU))

import play_core   # noqa: E402
import wsock       # noqa: E402

MIN_HOLD = play_core.MIN_HOLD


def client_frame(payload, opcode=wsock.TEXT, fin=True, mask=b'\x11\x22\x33\x44'):
    n = len(payload)
    b0 = (0x80 if fin else 0) | opcode
    if n < 126:
        head = struct.pack('!BB', b0, 0x80 | n)
    elif n < 1 << 16:
        head = struct.pack('!BBH', b0, 0x80 | 126, n)
    else:
        head = struct.pack('!BBQ', b0, 0x80 | 127, n)
    return head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload))


class WebSocket(unittest.TestCase):
    def test_accept_key_is_the_rfc_example(self):
        self.assertEqual(wsock.accept_key('dGhlIHNhbXBsZSBub25jZQ=='), 's3pPLMBiTxaQ9kYGzzhZRbK+xOo=')

    def test_every_length_class_round_trips(self):
        for n in (0, 5, 125, 126, 300, 65535, 70000):
            data = bytes(i % 251 for i in range(n))
            op, got = wsock.read_message(io.BytesIO(client_frame(data, wsock.BINARY)))
            self.assertEqual((op, got), (wsock.BINARY, data), n)

    def test_fragments_join_and_pings_in_between_are_answered(self):
        pings = []
        stream = (client_frame(b'{"c":', wsock.TEXT, fin=False) + client_frame(b'hi', wsock.PING)
                  + client_frame(b'"key"}', wsock.CONT, fin=True))
        op, got = wsock.read_message(io.BytesIO(stream), on_ping=pings.append)
        self.assertEqual((op, got, pings), (wsock.TEXT, b'{"c":"key"}', [b'hi']))

    def test_unmasked_client_frame_is_refused(self):
        with self.assertRaises(wsock.ProtocolError):
            wsock.read_message(io.BytesIO(struct.pack('!BB', 0x81, 2) + b'hi'))

    def test_close_and_eof(self):
        self.assertEqual(wsock.read_message(io.BytesIO(client_frame(b'', wsock.CLOSE))), (wsock.CLOSE, b''))
        with self.assertRaises(EOFError):
            wsock.read_message(io.BytesIO(b''))

    def test_server_frames_carry_the_right_length(self):
        for n, second in ((125, 125), (126, 126), (70000, 127)):
            f = wsock.encode(b'a' * n)
            self.assertEqual(f[0], 0x82)
            self.assertEqual(f[1], second)


class Keys(unittest.TestCase):
    def test_a_tap_shorter_than_a_frame_still_lasts_min_hold(self):
        inp = play_core.Input(['up'])
        inp.key('up', True, 10)
        inp.key('up', False, 10)
        for f in range(11, 10 + MIN_HOLD):
            inp.settle(f, None)
            self.assertIn('up', inp.held, f)
        inp.settle(10 + MIN_HOLD, None)
        self.assertNotIn('up', inp.held)

    def test_a_held_key_stays_down(self):
        inp = play_core.Input(['right'])
        inp.key('right', True, 0)
        for f in range(1, 200):
            inp.settle(f, None)
        self.assertIn('right', inp.held)

    def hold(self, speed, press_s, run_s, fps=56.4):
        """Hold 'up' for press_s seconds while the core runs at `speed` x real time.

        Returns how many frames the core actually saw the key down, and whether it was down
        at the end.
        """
        inp = play_core.Input(['up'], fps=fps)
        inp.key('up', True, 0, 0.0)
        down = 0
        frames = int(fps * speed * run_s)
        for f in range(1, frames + 1):
            now = f / (fps * speed)
            if now >= press_s and 'up' in inp.want:
                inp.key('up', False, f, now)
            inp.settle(f, None, now, fast=speed != 1)
            down += 'up' in inp.held
        return down, 'up' in inp.held

    def test_a_tap_at_x4_is_as_short_in_game_time_as_at_x1(self):
        """Reported 2026-09-16: at x4 and MAX one tap on "turn" turned the hero several times --
        the game counts frames, and 120 ms at x4 is 28 frames, enough for its own auto-repeat."""
        at1, _ = self.hold(1, 0.12, 1.0)
        at4, _ = self.hold(4, 0.12, 1.0)
        at_max, _ = self.hold(15, 0.12, 1.0)          # "MAX" is ~850 fps, i.e. ~15x
        self.assertLessEqual(at1, 10)
        self.assertGreaterEqual(at1, MIN_HOLD)
        self.assertLessEqual(at4, 12, 'a tap at x4 must not outlast its x1 length')
        self.assertLessEqual(at_max, 12, 'a tap at MAX must not outlast its x1 length')
        self.assertGreaterEqual(at4, MIN_HOLD)
        self.assertGreaterEqual(at_max, MIN_HOLD)

    def test_a_deliberate_hold_still_walks_on_fast_forward(self):
        down4, at_end = self.hold(4, 2.0, 2.0)
        self.assertTrue(at_end, 'a long hold must stay down at x4')
        self.assertGreater(down4, 0.5 * 4 * 56.4 * 2.0, 'most of the hold must reach the game')

    def test_x1_taps_are_not_chopped(self):
        down, _ = self.hold(1, 0.5, 0.6)
        self.assertGreater(down, 0.9 * 56.4 * 0.5)

    def test_unknown_key_is_ignored(self):
        inp = play_core.Input(['up'])
        self.assertFalse(inp.key('rm -rf', True, 0))
        self.assertEqual(inp.held, {})

    def test_release_all_lets_go_after_min_hold(self):
        inp = play_core.Input(['up', 'space'])
        inp.key('up', True, 0)
        inp.key('space', True, 0)
        inp.release_all()
        inp.settle(MIN_HOLD, None)
        self.assertEqual(inp.held, {})


class Clicks(unittest.TestCase):
    def frames_down(self, inp, start, end, cursor=None):
        n = 0
        for f in range(start, end):
            inp.settle(f, cursor)
            n += inp.btn['left']
        return n

    def test_a_click_faster_than_a_frame_reaches_the_game(self):
        """Regression 2026-09-16: down+up in one command batch never pressed the button, and
        a 3-frame press was too short for the engine's scripts; a click lasts CLICK_HOLD."""
        inp = play_core.Input([])
        inp.button('left', True, 5)
        inp.button('left', False, 5)
        self.assertEqual(self.frames_down(inp, 6, 40), play_core.CLICK_HOLD)
        self.assertGreaterEqual(play_core.CLICK_HOLD, 8)

    def test_a_double_click_is_two_presses(self):
        inp = play_core.Input([])
        inp.button('left', True, 0)
        inp.button('left', False, 0)
        inp.settle(1, None)
        inp.button('left', True, 1)
        inp.button('left', False, 1)
        presses, was = 0, inp.btn['left']
        for f in range(2, 40):
            inp.settle(f, None)
            presses += inp.btn['left'] and not was
            was = inp.btn['left']
        self.assertEqual(presses, 1)            # the second one; the first was already down

    def test_a_click_waits_for_the_cursor_to_arrive_and_settle(self):
        """Regression 2026-09-16: pressed the frame the cursor landed, "Load 1" ignored it."""
        inp = play_core.Input([])
        inp.aim(100, 100)
        inp.button('left', True, 0)
        inp.button('left', False, 0)
        inp.settle(1, (0, 0))
        self.assertFalse(inp.btn['left'])
        # ⚠️ Literal frames, not CLICK_SETTLE: a test derived from the constant passed with the
        # constant set to 0, i.e. with the defect put back (checked by breaking it).
        for f in (2, 3, 4, 5):                  # landed at 2; the measured need is 4 frames
            inp.settle(f, (100, 100))
            self.assertFalse(inp.btn['left'], f)
        for f in range(6, 12):
            inp.settle(f, (100, 100))
        self.assertTrue(inp.btn['left'])

    def test_a_click_with_no_aim_is_immediate(self):
        inp = play_core.Input([])
        inp.button('left', True, 0)
        inp.settle(1, (5, 5))
        self.assertTrue(inp.btn['left'])

    def test_a_click_does_not_wait_forever(self):
        inp = play_core.Input([])
        inp.aim(100, 100)
        inp.button('left', True, 0)
        inp.settle(play_core.CLICK_WAIT, (0, 0))
        self.assertTrue(inp.btn['left'])


def drive(inp, cursor, frames, reading=True, clamp=(639, 391)):
    """A stand-in for the engine: it applies the motion on each poll if it is reading the mouse."""
    cur, owed, sent = list(cursor), [0, 0], [0, 0]
    for f in range(frames):
        inp.steer(tuple(cur), f)
        d = inp.take_delta()
        sent[0] += abs(d[0])
        sent[1] += abs(d[1])
        owed[0] += d[0]
        owed[1] += d[1]
        if reading:
            cur = [max(0, min(clamp[0], cur[0] + owed[0])), max(0, min(clamp[1], cur[1] + owed[1]))]
            owed = [0, 0]
    return cur, sent


class Mouse(unittest.TestCase):
    def test_aim_converges(self):
        inp = play_core.Input([])
        inp.aim(320, 200)
        cur, _ = drive(inp, (639, 391), 10)
        self.assertEqual(cur, [320, 200])

    def test_an_aim_past_the_edge_sends_nothing_more(self):
        """The engine stops y at 391 (STATUS §19); the loop must not keep pushing.

        It owes one last correction after it sees the clamp (8 px here), then nothing: what
        is sent after 20 frames and after 200 is the same.
        """
        def run(frames):
            inp = play_core.Input([])
            inp.aim(10, 399)
            return drive(inp, (10, 10), frames)
        cur, early = run(20)
        _, late = run(200)
        self.assertEqual(cur, [10, 391])
        self.assertEqual(early, late)

    def test_a_game_not_reading_the_mouse_is_not_flooded(self):
        inp = play_core.Input([])
        inp.aim(300, 300)
        _, sent = drive(inp, (0, 0), 200, reading=False)
        self.assertEqual(sent, [300, 300])

    def test_an_arrived_aim_lets_go_of_the_cursor(self):
        """Regression 2026-09-16: a standing aim dragged the cursor back after the game's own
        arrow keys had moved it (menus, selection mode), so the keyboard lost the cursor."""
        inp = play_core.Input([])
        inp.aim(320, 200)
        cur, _ = drive(inp, (0, 0), 10)
        self.assertEqual(cur, [320, 200])
        # now the game moves its cursor itself (an arrow key); nothing may pull it back
        _, sent = drive(inp, (250, 200), 30)
        self.assertEqual(sent, [0, 0])

    def test_a_new_aim_steers_again(self):
        inp = play_core.Input([])
        inp.aim(320, 200)
        drive(inp, (0, 0), 10)
        inp.aim(100, 100)
        cur, _ = drive(inp, (320, 200), 10)
        self.assertEqual(cur, [100, 100])

    def test_a_click_after_steering_gave_up_still_happens(self):
        inp = play_core.Input([])
        inp.aim(10, 399)
        drive(inp, (10, 10), 40)                 # clamped at 391: steering gives up
        self.assertFalse(inp.steering)
        inp.button('left', True, 100)
        inp.button('left', False, 100)
        for f in range(101, 101 + 12):
            inp.settle(f, (10, 391))
        self.assertTrue(inp.btn['left'] or inp.btn_since['left'] > 100)

    def test_relative_motion_cancels_the_aim(self):
        inp = play_core.Input([])
        inp.aim(5, 5)
        inp.move(3, -2)
        self.assertIsNone(inp.target)
        self.assertEqual(inp.take_delta(), [3, -2])


class Pacing(unittest.TestCase):
    def test_x1_is_the_cores_own_rate(self):
        p = play_core.Pace(56.4)
        self.assertAlmostEqual(p.period(1), 1 / 56.4)
        self.assertAlmostEqual(p.period(4), 1 / 225.6)
        self.assertEqual(p.period(0), 0.0)

    def test_audio_adjust_is_bounded_and_centred(self):
        self.assertEqual(play_core.audio_adjust(3), 1.0)
        self.assertEqual(play_core.audio_adjust(100), 1.01)
        self.assertEqual(play_core.audio_adjust(0), 0.99)


class Cockpit(unittest.TestCase):
    def test_frame_payload_drops_row_padding(self):
        import play
        pix = bytes([1, 0, 2, 0, 9, 9, 3, 0, 4, 0, 9, 9])          # 2x2, pitch 6
        out = play.Hub._frame_payload(pix, 2, 2, 6, 7)
        magic, w, h, pitch, _, seq = play.FRAME_HEAD.unpack_from(out, 0)
        self.assertEqual((magic, w, h, pitch, seq), (b'WWF1', 2, 2, 4, 7))
        self.assertEqual(out[play.FRAME_HEAD.size:], bytes([1, 0, 2, 0, 3, 0, 4, 0]))
        self.assertEqual(play.FRAME_HEAD.size, 16)              # play.js reads pixels at 16

    def test_a_second_cockpit_leaves_the_live_pid_file_alone(self):
        """Regression 2026-09-16: play_live_test.py's cockpit overwrote emu/play.pid and deleted
        it on exit; the live cockpit was left without one."""
        import os
        import subprocess
        code = 'import play; print(play.PIDFILE)'
        env = {**os.environ, 'WW_PLAY_PORT': '9999', 'WW_PLAY_STATES': '/tmp/ww-states-test'}
        other = subprocess.run([sys.executable, '-c', code], cwd=EMU, env=env,
                               capture_output=True, text=True).stdout.strip()
        env = {k: v for k, v in os.environ.items() if k not in ('WW_PLAY_PORT', 'WW_PLAY_STATES')}
        live = subprocess.run([sys.executable, '-c', code], cwd=EMU, env=env,
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(live, str(EMU / 'play.pid'))
        self.assertNotEqual(other, live)

    def test_being_in_the_game_is_decided_without_the_translation_sources(self):
        """Regression 2026-09-16: "are we in the game" came from identify(), which needs `en/`.
        In the public copy that answer was always no, so the hero, the position, the map and
        autobattle were all dead -- though none of them reads a script."""
        import play
        hero = {'hp': 702, 'hp_max': 702, 'level': 36}
        self.assertTrue(play.playing('YADO.MES', hero), 'in the inn with a real save')
        self.assertTrue(play.playing('FLOOR5A.MES', hero))
        self.assertFalse(play.playing('START1.MES', hero), 'the title menu is not the game')
        self.assertFalse(play.playing('YADO.MES', {'hp': 11, 'hp_max': 11, 'level': 0}),
                         'level 0 is the new-game block, not a save')
        self.assertFalse(play.playing(None, hero))
        self.assertFalse(play.playing('YADO.MES', None))
        # with the scripts present the sharp answer still wins
        self.assertTrue(play.playing('anything', None, stack=['START.MES', 'FLOOR05.MES']))
        self.assertFalse(play.playing('anything', hero, stack=['START1.MES']))

    def test_the_sensors_work_without_the_translation_sources(self):
        """Regression 2026-09-16: `state` indexed `en/` at import and raised when it was absent.
        The public copy ships without `en/` -- that IS the translation -- so every sensor in the
        cockpit died there: HP, coordinates, items, the map. None of them reads a script; only
        identify() does, and it may honestly answer "I don't know"."""
        import pathlib as _p
        import state
        keep = (state.SCRIPTS, state.ORIGINALS)
        try:
            state.SCRIPTS = _p.Path('/nonexistent-en')
            state.ORIGINALS = _p.Path('/nonexistent-work')
            self.assertEqual(state._load_scripts(), {}, 'no scripts is empty, not an error')
        finally:
            state.SCRIPTS, state.ORIGINALS = keep
        blob = bytes(0x40000)
        self.assertIsNotNone(state.stats(blob), 'stats read memory, not scripts')
        self.assertIsNotNone(state.where(blob))

    def test_play_has_its_own_emulator_directories(self):
        """The core rewrites np2kai.cfg on exit; sharing it has killed a live game (STATUS §49)."""
        import os
        import play
        if not os.environ.get('WW_SYSTEM'):
            self.assertNotIn(play.SYSTEM.name, ('system', 'system-agent'))
        if not os.environ.get('WW_SAVE'):
            self.assertNotIn(play.SAVE_DIR.name, ('save', 'save-agent'))



class Hero(unittest.TestCase):
    """One hero with two names: the card shows the one he goes by now."""

    def snap(self, named):
        import state
        b = bytearray(state.REG_BASE + 0x400)
        n = state.NAMED_POLLUX
        b[state.REG_BASE + n // 2] = (named << 4) if n % 2 else named
        return b

    def test_the_name_follows_the_story(self):
        import state
        self.assertEqual(state.hero(self.snap(0), 'FLOOR03.MES'), 'Astral')
        self.assertEqual(state.hero(self.snap(0), 'FLOOR5AB.MES'), 'Astral')
        self.assertEqual(state.hero(self.snap(0), 'SHP_4S.MES'), 'Astral')
        self.assertIsNone(state.hero(self.snap(0), 'TOWN.MES'), 'nameless until Fabrice names him')
        self.assertEqual(state.hero(self.snap(1), 'TOWN.MES'), 'Pollux')
        self.assertEqual(state.hero(self.snap(1), 'FLOOR08.MES'), 'Pollux')
        self.assertIsNone(state.hero(self.snap(0), 'FLOOR00.MES'))

    def test_first_half_matches_the_scripts(self):
        """The split must be the scripts' own: `"[" 0 "]:"` is Astral, `"[" 1 "]:"` is Pollux."""
        import re
        import state
        en = EMU.parent / 'en'
        if not en.is_dir():
            self.skipTest('no en/ here (public copy)')
        tag = re.compile(r'"\[" ([01]) "\]:')
        checked = 0
        for f in en.glob('*.MES.rkt'):
            used = set(tag.findall(f.read_text(encoding='utf-8')))
            if len(used) != 1:
                continue                     # neither, or both (TOWN: the scene where he remembers)
            name = f.name[:-len('.rkt')]
            self.assertEqual(bool(state.FIRST_HALF.match(name)), used == {'0'}, name)
            checked += 1
        self.assertGreater(checked, 40)


if __name__ == '__main__':
    unittest.main()
