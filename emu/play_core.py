#!/usr/bin/env python3
"""The emulator process behind the play cockpit: the core, its pace, input and sound. Nothing else.

Why a process of its own (measured 2026-09-16 on the old cockpit, emu/agent.py in human mode):
  * the core shared ONE thread with a PNG encode of every changed frame (~10-15 ms), a 10.8 MB
    serialize eight times a second (11 ms each), file polling for input and the agent's own
    bookkeeping. At "max" speed the game's pace therefore followed the screen content and the
    CPU load; at x1 each key press ran 42 frames unpaced, and input was read once per 20 frames
    (355 ms at x1).
  * a core crash or a Python exception in that loop froze the picture while the process stayed
    up, and nothing said so.
Here the core owns a process: it runs one frame, publishes the picture to shared memory, sleeps
to the next deadline of the real 56.4 Hz clock. Everything else -- serving the page, telemetry,
the map -- lives in the parent (emu/play.py) and cannot slow a frame down. If the core dies, the
parent sees the exit code and says so.

Input is live: a key is DOWN while it is held in the browser, like a real keyboard, instead of
a fixed 6-frame tap. The mouse is the core's real mouse device (1 mickey = 1 pixel, measured),
driven to an ABSOLUTE target: the engine keeps its cursor at CURSOR in memory, and the loop
closes on it -- point at the screen, the game's cursor goes there.
"""
import os
import pathlib
import json
import queue
import struct
import sys
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

CORE = '/usr/lib/libretro/np2kai_libretro.so'

# Shared frame buffer: header + RGB565 pixels. The header is a seqlock: the writer makes `seq`
# odd, copies, makes it even; a reader that saw the same even value before and after its copy
# has a whole frame.
FRAME_HDR = struct.Struct('<IHHHH')          # seq, width, height, pitch, reserved
FRAME_OFF = 16
FRAME_BYTES = 1024 * 1024 * 2                # any PC-98 mode fits; 640x400x2 is what we get
SHM_SIZE = FRAME_OFF + FRAME_BYTES

LOW = 0x40000        # the whole game lives below this: map, coordinates, stats (emu/state.py)
CURSOR = 0x87ac      # u16 x, u16 y: the engine's mouse cursor in screen pixels (0..639, 0..399).
                     # Found 2026-09-16 by moving the core's mouse on the title menu: the only
                     # words that tracked 48-pixel steps on one axis and stood still on the
                     # other. A mirror sits at 0x1785a (inside the save area).
MIN_HOLD = 3         # frames a key or button stays down however short the real press was: a
                     # tap shorter than one input poll would never reach the game
CLICK_HOLD = 8       # frames a mouse button stays down per click. The engine's scripts poll the
                     # button far less often than the keyboard: a 3-frame click on "Load 1" was
                     # ignored, a 9-frame one loaded the game (measured 2026-09-16)
# ⚠️ A key is held in REAL time by the player, but the game counts FRAMES. At x4 the same
# 120 ms tap lasts 28 frames instead of 7, the game's own auto-repeat fires, and one tap on
# "turn" turned the hero two or three times (reported 2026-09-16 while playing). So a SHORT tap
# is clamped to what it would have lasted at x1, and only a deliberate hold (longer than
# HOLD_INTENT on the real clock) is handed to the game as a continuous press -- that is what
# walking on fast-forward needs.
HOLD_INTENT = 0.3    # s of real holding after which the player clearly means "keep it down"
TAP_SLACK = 1.25     # tolerance over the x1 frame budget, for pacing jitter
CLICK_SETTLE = 4     # frames the cursor must already sit on its aim before the button goes down.
                     # A browser click arrives together with the aim; pressed the very frame the
                     # cursor landed, "Load 1" ignored it -- the menu picks the item under the
                     # cursor from an earlier poll. Hovering first (400 ms) loaded the game.
CLICK_WAIT = 24      # frames a click may wait for the cursor to reach its aim and settle
STEER_PATIENCE = 3   # corrections the cursor may ignore before steering lets go of it
TURBO_PUBLISH = 1 / 60
VALIDATE_EVERY = 20.0   # s between checks that the in-place memory view still is the memory
SPEEDS = (1, 2, 4, 0)   # x1, x2, x4, unthrottled


def _mem_fd():
    return os.open('/proc/self/mem', os.O_RDONLY)


def _addr(buf):
    """Address of a bytes-like object's data (to exclude our own copies from the memory search)."""
    import numpy as np
    return np.frombuffer(buf, dtype=np.uint8).ctypes.data


HEAD = 0x800         # serialize() opens with its own header over the first ~0x680 bytes; the
                     # memory copy agrees with the live array only above that (measured). Every
                     # address we read is far above it.


def locate_memory(serialize, step, fd, needle_off=0x8780, tries=240):
    """Where the core keeps the emulated low memory, so it can be READ IN PLACE.

    np2kai exports no symbol for it and its retro_get_memory_data() returns extended memory,
    not the 640 KB the game lives in. But serialize() carries a plain copy of it at the same
    offsets, so the same bytes sit somewhere in our address space. A search finds several
    places (measured): the live array, stale copies of earlier snapshots, and a scratch buffer
    that serialize() itself rewrites. The live one is the only one that CHANGES WHILE FRAMES RUN
    with no serialize in between -- and then equals a fresh snapshot above HEAD.

    `serialize()` -> bytes, `step()` runs one frame. Returns the base address or None.
    """
    b = serialize()
    needle = b[needle_off:needle_off + 256]
    if sum(1 for x in needle if x) < 16:
        return None
    own = [(_addr(b), len(b))]
    hits = []
    for line in open('/proc/self/maps'):
        parts = line.split()
        lo, hi = (int(x, 16) for x in parts[0].split('-'))
        path = parts[5] if len(parts) > 5 else ''
        if 'rw' not in parts[1] or (path and not path.startswith('[heap')) or hi - lo > 256 << 20:
            continue
        try:
            blob = os.pread(fd, hi - lo, lo)
        except OSError:
            continue
        i = blob.find(needle)
        while i >= 0:
            base = lo + i - needle_off
            if not any(a <= base < a + n for a, n in own):
                hits.append(base)
            i = blob.find(needle, i + 1)
    del b
    for _ in range(tries):
        before = {h: os.pread(fd, LOW - HEAD, h + HEAD) for h in hits}
        step()
        moved = [h for h in hits if os.pread(fd, LOW - HEAD, h + HEAD) != before[h]]
        if not moved:
            continue                        # an idle frame; run another
        c = serialize()
        live = [h for h in moved if os.pread(fd, LOW - HEAD, h + HEAD) == c[HEAD:LOW]]
        return live[0] if len(live) == 1 else None
    return None


class Input:
    """What the core sees on its input poll: held keys, mouse motion and buttons.

    Pure bookkeeping, no core in here -- tested in emu/tests/test_play.py.
    """

    def __init__(self, valid_keys, fps=56.4):
        self.valid = set(valid_keys)
        self.fps = float(fps) or 56.4
        self.want = set()             # keys the player holds right now
        self.held = {}                # key -> frame it went down, as the core sees it
        self.pressed_at = {}          # key -> (frame, real time) the player pressed it
        self.paused = set()           # keys let go early because the tap was short at x2/x4/MAX
        self._until = {}              # key -> frame a scripted tap ends
        self.btn_want = {'left': False, 'right': False}
        self.btn = {'left': False, 'right': False}
        self.btn_since = {'left': 0, 'right': 0}
        self.btn_asked = {'left': 0, 'right': 0}
        # ⚠️ A click owed to the core. The browser sends "down" and "up" within milliseconds;
        # both used to land before the next frame, the button was never down for a single
        # poll, and clicking a title-menu item did nothing (caught 2026-09-16).
        self.btn_pending = {'left': False, 'right': False}
        self.target = None            # absolute cursor aim, screen pixels
        self.steering = False         # still driving the cursor toward `target`
        self._still = 0               # corrections in a row that the cursor ignored
        self._aimed_since = None      # frame the cursor arrived on its aim
        self.delta = [0, 0]           # relative motion owed to the next poll
        self._inflight = [0, 0]
        self._seen = None
        self._fix_at = 0
        self.changed = True           # the keyboard set changed: rebuild the core's state

    def tap(self, name, frames, frame):
        """Hold a key for exactly `frames` emulator frames -- the unit a script thinks in.

        Autobattle runs in the cockpit process and must not care how fast the core is going:
        a tap of N frames is the same press at x1 and at MAX.
        """
        if name not in self.valid:
            return False
        if name not in self.held:
            self.held[name] = frame
            self.changed = True
        self.want.add(name)
        self.pressed_at[name] = (frame, None)          # frames, not the clock: no tap clamp
        self._until[name] = frame + max(1, int(frames))
        return True

    def key(self, name, down, frame, now=None):
        if name not in self.valid:
            return False
        if down:
            if name not in self.want:
                self.pressed_at[name] = (frame, now)
            self.want.add(name)
            if name not in self.held:
                self.held[name] = frame
                self.changed = True
        else:
            self.want.discard(name)
            self.paused.discard(name)
            self.pressed_at.pop(name, None)
        return True

    def button(self, name, down, frame):
        if name in self.btn_want:
            self.btn_want[name] = down
            if down:
                self.btn_asked[name] = frame
                self.btn_pending[name] = True

    def release_all(self):
        self.want.clear()
        self.paused.clear()
        self.pressed_at.clear()
        self._until.clear()
        self.btn_want = {'left': False, 'right': False}
        self.btn_pending = {'left': False, 'right': False}

    def aim(self, x, y):
        """Steer the cursor to (x, y) ONCE: until it arrives, or the engine stops moving it.

        ⚠️ Not a leash. Where the game moves its own cursor -- arrow keys in its menus park it
        (STATUS §19), a selection mode walks it with the arrows -- a standing aim dragged it
        straight back to wherever the mouse pointer last was, and the keyboard lost the cursor.
        The page sends a new aim on every pointer move and with every click.
        """
        self.target = (int(x), int(y))
        self.steering = True
        self._seen = None
        self._inflight = [0, 0]
        self._fix_at = 0
        self._still = 0

    def move(self, dx, dy):
        self.target = None
        self.steering = False
        self.delta[0] += int(dx)
        self.delta[1] += int(dy)

    def steer(self, cursor, frame):
        """Close the loop on the engine's cursor: owe the core the distance still to go.

        `_inflight` is motion already handed over but not yet seen in the cursor -- without it
        a game that is not reading the mouse right now (the dungeon view) would be handed the
        same distance every two frames and the cursor would fly to the edge once it looks.
        At an edge the engine clamps (y stops at 391, STATUS §19); the owed motion then stays
        "in flight" and nothing more is sent.
        """
        if self.target is None or not self.steering or frame < self._fix_at:
            return
        if cursor is None:                      # memory not located: open loop, 1:1 measured
            if self._seen is not None:
                self.delta[0] += self.target[0] - self._seen[0]
                self.delta[1] += self.target[1] - self._seen[1]
            self._seen = self.target
            self.steering = False
            return
        if cursor == self.target:
            self.steering = False               # arrived: let go of the cursor
            return
        if cursor != self._seen:
            self._seen = cursor
            self._inflight = [0, 0]
            self._still = 0
        else:
            self._still += 1
            if self._still > STEER_PATIENCE:    # clamped at an edge, or the game is not reading
                self.steering = False
                return
        dx = self.target[0] - cursor[0] - self._inflight[0]
        dy = self.target[1] - cursor[1] - self._inflight[1]
        if dx or dy:
            self.delta[0] += dx
            self.delta[1] += dy
            self._inflight[0] += dx
            self._inflight[1] += dy
            self._fix_at = frame + 2

    def aimed(self, cursor):
        """Nothing more will move the cursor for this aim: it arrived, or steering gave up."""
        return (self.target is None or cursor is None or not self.steering
                or (abs(cursor[0] - self.target[0]) <= 2 and abs(cursor[1] - self.target[1]) <= 2))

    def settle(self, frame, cursor, now=None, fast=False):
        """After a frame: release what was let go (not before MIN_HOLD), keep taps as short in
        GAME time as they were on the clock, press buttons once aimed."""
        for k in [k for k, end in self._until.items() if frame >= end]:
            del self._until[k]
            self.want.discard(k)
            self.pressed_at.pop(k, None)
        for k in [k for k in self.held if k not in self.want and frame - self.held[k] >= MIN_HOLD]:
            del self.held[k]
            self.changed = True
        if now is not None:
            for k in list(self.want):
                f0, t0 = self.pressed_at.get(k, (frame, now))
                if t0 is None:
                    continue
                wall, frames = now - t0, frame - f0
                if k in self.held:
                    # running faster than the game: a short tap must not outlast its x1 length
                    if (fast and wall < HOLD_INTENT and frames >= MIN_HOLD
                            and frames > wall * self.fps * TAP_SLACK + MIN_HOLD):
                        del self.held[k]
                        self.paused.add(k)
                        self.changed = True
                elif k in self.paused and wall >= HOLD_INTENT:
                    self.held[k] = frame            # still holding: they mean it, press again
                    self.paused.discard(k)
                    self.changed = True
        if self.aimed(cursor):
            if self._aimed_since is None:
                self._aimed_since = frame
        else:
            self._aimed_since = None
        settled = self._aimed_since is not None and (
            self.target is None or cursor is None or frame - self._aimed_since >= CLICK_SETTLE)
        for b in self.btn:
            if self.btn_pending[b] and not self.btn[b]:
                if settled or frame - self.btn_asked[b] >= CLICK_WAIT:
                    self.btn[b] = True
                    self.btn_since[b] = frame
                    self.btn_pending[b] = False
            elif not self.btn_want[b] and self.btn[b] and frame - self.btn_since[b] >= CLICK_HOLD:
                self.btn[b] = False

    def take_delta(self):
        d, self.delta = self.delta, [0, 0]
        return d


class Pace:
    """Deadlines on the real clock: x1 is the core's own 56.4 Hz, not 60 (a 6.4% error once put
    6.4 s of sound into 6.0 s). Falling behind by more than a quarter second resets the clock
    instead of sprinting to catch up."""

    def __init__(self, fps):
        self.fps = fps
        self.next = time.perf_counter()

    def period(self, speed):
        return 0.0 if not speed else 1.0 / (self.fps * speed)

    def wait(self, speed, adjust=1.0):
        p = self.period(speed) * adjust
        now = time.perf_counter()
        if not p:
            self.next = now
            return 0.0
        self.next += p
        delay = self.next - now
        if delay > 0:
            time.sleep(delay)
        elif delay < -0.25:
            self.next = now
        return delay


def audio_adjust(queued, target=3):
    """Dynamic rate control, as RetroArch does it: the frame period stretches by up to 1% while
    our audio queue is above target and shrinks while below, so the core's sample rate follows
    the sound card's clock and the queue neither drains (clicks) nor overflows (drops)."""
    return 1.0 + 0.01 * max(-1.0, min(1.0, (queued - target) / 3.0))


class Machine:
    def __init__(self, cfg, status):
        import numpy as np
        from multiprocessing import shared_memory
        from libretro import Session
        from libretro.drivers import ExplicitPathDriver, IterableInputDriver, DictOptionDriver
        from libretro.api.input.keyboard import KeyboardState
        from libretro.api.input.mouse import MouseState
        from libretro.drivers.input.iterable import PortState
        import sound

        self.np = np
        self.cfg = cfg
        self.status = status
        self.KeyboardState, self.MouseState, self.PortState = KeyboardState, MouseState, PortState
        self.shm = shared_memory.SharedMemory(name=cfg['shm'])
        self.frame_no = 0
        self.seq = 0
        self.speed = cfg.get('speed', 1)
        self.turbo = False
        self.paused = False
        self.sound_on = bool(cfg.get('sound', True))
        self.volume = float(cfg.get('volume', 0.8))
        self._last_pub = None
        self._pub_at = 0.0
        self._kb = None
        self.input = Input(KeyboardState.__init__.__annotations__, fps=56.4)
        self.trace = bool(cfg.get('trace'))
        self.mem_base = None
        self.mem_fd = _mem_fd()
        self._validated = time.time()

        machine = self

        class Driver(sound.Driver):
            def sample_batch(self, frames):
                view = frames if getattr(frames, 'format', 'h') == 'h' else frames.cast('B').cast('h')
                if machine.volume < 0.999:
                    a = (np.frombuffer(view, dtype=np.int16) * machine.volume).astype(np.int16)
                    self.speaker.feed(a.tobytes())
                else:
                    self.speaker.feed(view.tobytes())
                return len(view) // 2

        self.speaker = sound.Speaker(latency=cfg.get('latency', '120ms'))
        path = ExplicitPathDriver(corepath=CORE, system=cfg['system'], save=cfg['save'],
                                  assets=cfg['system'])
        # ...and the same lines arrive a second time through the libretro log callback. Left
        # to itself libretro.py makes a DEBUG logger with a stderr handler; this one is mute.
        import logging
        quiet = logging.getLogger('ww.core')
        quiet.setLevel(logging.CRITICAL)
        quiet.propagate = False
        quiet.addHandler(logging.NullHandler())
        self.sess = Session(core=CORE, game=cfg['disk'], path=path, audio=Driver(self.speaker),
                            input=IterableInputDriver(self._poll), options=DictOptionDriver(2),
                            log=quiet, vfs=None)
        self.sess.__enter__()
        self.core = self.sess.core
        av = self.core.get_system_av_info()
        self.fps = float(av.timing.fps) or 56.4
        self.aspect = float(av.geometry.aspect_ratio or 0) or 1.6
        self.pace = Pace(self.fps)

    # ---- input ---------------------------------------------------------------------------
    def _poll(self):
        while True:
            if self.input.changed:
                self._kb = (self.KeyboardState(**{k: True for k in self.input.held})
                            if self.input.held else None)
                self.input.changed = False
            dx, dy = self.input.take_delta()
            b = self.input.btn
            yield self.PortState(keyboard=self._kb,
                                 mouse=self.MouseState(x=dx, y=dy, left=b['left'], right=b['right']))

    def cursor(self):
        if self.mem_base is None:
            return None
        try:
            raw = os.pread(self.mem_fd, 4, self.mem_base + CURSOR)
        except OSError:
            return None
        return struct.unpack('<HH', raw)

    # ---- frames --------------------------------------------------------------------------
    def serialize(self):
        buf = bytearray(self.core.serialize_size())
        return bytes(buf) if self.core.serialize(buf) else None

    def step(self):
        cur = self.cursor()
        self.input.steer(cur, self.frame_no)
        self.sess.run()
        self.frame_no += 1
        was = dict(self.input.btn) if self.trace else None
        keys_was = set(self.input.held) if self.trace else None
        self.input.settle(self.frame_no, self.cursor(), time.monotonic(),
                          fast=self.turbo or self.speed != 1)
        if self.trace and keys_was != set(self.input.held):
            self.say({'t': 'log', 'msg': f'frame {self.frame_no}: keys {sorted(self.input.held)}'
                                         f' (was {sorted(keys_was)})'})
        if self.trace and was != self.input.btn:
            self.say({'t': 'log', 'msg': f'frame {self.frame_no}: buttons {self.input.btn} '
                                         f'cursor {self.cursor()} aim {self.input.target}'})

    def publish(self, force=False):
        av = self.sess.video._current
        d = av._frame_dims
        if d is None or not av._frame:
            return
        now = time.perf_counter()
        if not force and (self.turbo or self.speed != 1) and now - self._pub_at < TURBO_PUBLISH:
            return
        n = d.height * d.pitch
        pix = bytes(memoryview(av._frame)[:n])
        if pix == self._last_pub and not force:
            return
        self._last_pub = pix
        self._pub_at = now
        buf = self.shm.buf
        self.seq += 1
        FRAME_HDR.pack_into(buf, 0, 2 * self.seq - 1, d.width, d.height, d.pitch, 0)
        buf[FRAME_OFF:FRAME_OFF + n] = pix
        FRAME_HDR.pack_into(buf, 0, 2 * self.seq, d.width, d.height, d.pitch, 0)
        self.say({'t': 'frame', 'seq': self.seq}, block=False)

    # ---- messages ------------------------------------------------------------------------
    def say(self, msg, block=False):
        try:
            self.status.put(msg, block=block, timeout=1 if block else None)
        except queue.Full:
            pass

    def handle(self, m):
        c = m.get('c')
        inp = self.input
        if self.trace and c in ('key', 'tap'):
            # ⚠️ Logged HERE, not in settle(): a key goes down while the command is handled, so
            # comparing the held set before and after a frame only ever showed releases.
            self.say({'t': 'log', 'msg': f'frame {self.frame_no}: {json.dumps(m)}'})
        if c == 'key':
            inp.key(m.get('k'), bool(m.get('d')), self.frame_no, time.monotonic())
        elif c == 'tap':
            ok = inp.tap(m.get('k'), m.get('frames', 8), self.frame_no)
            if self.trace:
                self.say({'t': 'log', 'msg': f'frame {self.frame_no}: tap accepted={ok}'})
        elif c == 'release_all':
            inp.release_all()
            self.turbo = False
        elif c == 'aim':
            inp.aim(m['x'], m['y'])
        elif c == 'move':
            inp.move(m.get('dx', 0), m.get('dy', 0))
        elif c == 'btn':
            inp.button(m.get('b'), bool(m.get('d')), self.frame_no)
        elif c == 'speed' and m.get('v') in SPEEDS:
            self.speed = m['v']
        elif c == 'turbo':
            self.turbo = bool(m.get('d'))
        elif c == 'pause':
            self.paused = bool(m.get('d'))
        elif c == 'sound':
            self.sound_on = bool(m.get('on', self.sound_on))
            self.volume = max(0.0, min(1.0, float(m.get('vol', self.volume))))
        elif c == 'save':
            self._save(m['path'], m.get('id'))
        elif c == 'load':
            self._load(m['path'], m.get('id'))
        elif c == 'reboot':
            self.boot()
        elif c == 'quit':
            raise SystemExit(0)

    def _save(self, path, rid):
        s = self.serialize()
        ok = False
        if s:
            tmp = pathlib.Path(path).with_suffix('.tmp')
            tmp.write_bytes(s)
            tmp.replace(path)
            ok = True
        self.say({'t': 'saved', 'id': rid, 'ok': ok, 'path': str(path)}, block=True)

    def _load(self, path, rid):
        ok = False
        try:
            ok = bool(self.core.unserialize(pathlib.Path(path).read_bytes()))
        except Exception as e:
            self.say({'t': 'log', 'msg': f'state load failed: {e}'})
        self.input.release_all()
        self.pace.next = time.perf_counter()
        self.step()
        self.publish(force=True)
        self.say({'t': 'loaded', 'id': rid, 'ok': ok, 'path': str(path)}, block=True)

    # ---- sound ---------------------------------------------------------------------------
    def apply_sound(self):
        want = self.sound_on and not self.paused and not self.turbo and self.speed == 1
        if want and not self.speaker.on:
            self.speaker.start()
        elif not want and self.speaker.on:
            self.speaker.stop()

    def queued(self):
        q = self.speaker._q
        return q.qsize() if q is not None else 0

    # ---- boot ----------------------------------------------------------------------------
    def boot(self):
        """Cold boot to the title menu, as fast as the core goes; the player picks a slot."""
        import boot as recipe
        self.say({'t': 'boot', 'stage': 'starting'})
        self.core.reset()
        self.input.release_all()

        def run(n):
            for _ in range(n):
                self.step()
                if self.frame_no % 8 == 0:
                    self.publish()

        def press(key, hold=8, then=36):
            self.input.key(key, True, self.frame_no)
            run(hold)
            self.input.key(key, False, self.frame_no)
            run(then)

        def frame():
            av = self.sess.video._current
            d = av._frame_dims
            if d is None or not av._frame:
                return None
            return self.np.frombuffer(av._frame, dtype=self.np.uint16)[:d.height * d.pitch // 2].copy()

        was = self.turbo
        self.turbo = True
        try:
            recipe.drive(press, run, frame, log=lambda m: None, until='settle to title')
        finally:
            self.turbo = was
        if self.mem_base is None:
            self.mem_base = locate_memory(self.serialize, self.step, self.mem_fd)
            self._validated = time.time()
        self.publish(force=True)
        self.pace.next = time.perf_counter()
        self.say({'t': 'boot', 'stage': 'title', 'mem': self.mem_base})

    def validate(self):
        """Is the in-place view still the memory? Compared against a real snapshot."""
        if self.mem_base is None:
            return
        s = self.serialize()
        try:
            same = (s is not None and
                    os.pread(self.mem_fd, LOW - HEAD, self.mem_base + HEAD) == s[HEAD:LOW])
        except OSError:
            same = False
        if not same:
            self.say({'t': 'log', 'msg': 'memory view went stale; searching again'})
            self.mem_base = locate_memory(self.serialize, self.step, self.mem_fd)
            self.say({'t': 'mem', 'mem': self.mem_base})

    # ---- main loop -----------------------------------------------------------------------
    def loop(self, commands):
        parent = os.getppid()
        frames_at, t_at = self.frame_no, time.perf_counter()
        last_stat = 0.0
        while True:
            while True:
                try:
                    self.handle(commands.get_nowait())
                except queue.Empty:
                    break
            self.apply_sound()
            if self.paused:
                time.sleep(0.01)
                self.pace.next = time.perf_counter()
            else:
                self.step()
                self.publish()
                sp = 0 if self.turbo else self.speed
                adj = audio_adjust(self.queued()) if self.speaker.on else 1.0
                self.pace.wait(sp, adj)
            now = time.perf_counter()
            if now - last_stat >= 0.5:
                fps = (self.frame_no - frames_at) / (now - t_at)
                frames_at, t_at, last_stat = self.frame_no, now, now
                self.say({'t': 'stat', 'fps': round(fps, 1), 'frame': self.frame_no,
                          'speed': self.speed, 'turbo': self.turbo, 'paused': self.paused,
                          'sound': self.speaker.on, 'dropped': self.speaker.dropped,
                          'queued': self.queued(), 'cursor': self.cursor(), 'mem': self.mem_base,
                          'aspect': self.aspect, 'core_fps': self.fps})
                if os.getppid() != parent:
                    raise SystemExit(0)          # the cockpit is gone; don't run headless
            if time.time() - self._validated > VALIDATE_EVERY:
                self._validated = time.time()
                self.validate()

    def close(self):
        try:
            self.speaker.stop()
        except Exception:
            pass
        try:
            self.sess.__exit__(None, None, None)
        except Exception:
            pass
        self.shm.close()


def main(cfg, commands, status):
    """Entry point of the child process (multiprocessing, spawn)."""
    # np2kai prints "Open file (RO) %s." ~48 times a second, to both stdout and stderr; in the
    # old setup that grew agent_panel.log to 231 MB. The core's C streams go nowhere; Python's
    # own stderr keeps a private copy of the real one, so a traceback still reaches the log.
    real_err = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    sys.stderr = os.fdopen(real_err, 'w', buffering=1)
    sys.stdout = sys.stderr
    m = None
    try:
        m = Machine(cfg, status)
        status.put({'t': 'hello', 'pid': os.getpid(), 'fps': m.fps, 'aspect': m.aspect})
        m.boot()
        m.loop(commands)
    except SystemExit:
        pass
    except BaseException:
        status.put({'t': 'crash', 'trace': traceback.format_exc()[-2000:]})
        raise
    finally:
        if m is not None:
            m.close()
