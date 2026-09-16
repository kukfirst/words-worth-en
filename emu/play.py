#!/usr/bin/env python3
"""Words Worth play cockpit: the game screen, live telemetry, the floor map and the controls.

    emu/.venv/bin/python emu/play.py          # then open http://127.0.0.1:8778/
    emu/play.sh                               # the same, plus opens the browser

Two processes. The emulator (emu/play_core.py) runs the core at the real 56.4 Hz and does
nothing else; this one serves the page, pushes every changed frame over a WebSocket, reads the
game's memory IN PLACE for telemetry and the map, keeps save slots, and restarts the emulator
if it dies. None of the work here can slow a frame down: it happens in another process.

This replaces emu/agent.py + emu/viewer.py + emu/mapper.py for PLAYING. Those three stay for
the translation QA harness; they must not run on the same image at the same time (two writers
on one .hdi is how saves get lost) -- startup refuses if the agent holds this disk.
"""
import datetime
import http.server
import json
import logging
import logging.handlers
import multiprocessing as mp
import os
import pathlib
import queue
import shutil
import signal
import socketserver
import struct
import sys
import threading
import time
from multiprocessing import shared_memory

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import play_core   # noqa: E402
import play_grind  # noqa: E402
import wsock       # noqa: E402

GAME = HERE.parent / 'game'
DISK = pathlib.Path(os.environ.get('WW_DISK') or GAME / 'WordsWorth_play.hdi').resolve()
# ⚠️ Own system and save directories: the core rewrites np2kai.cfg in the system directory on
# exit, and a shared one has killed a live game once (STATUS §49). Checked by isolation_test.
SYSTEM = pathlib.Path(os.environ.get('WW_SYSTEM') or HERE / 'system-play')
SAVE_DIR = pathlib.Path(os.environ.get('WW_SAVE') or HERE / 'save-play')
STATES = pathlib.Path(os.environ.get('WW_PLAY_STATES') or HERE / 'play_states')
BACKUPS = DISK.parent / 'backups'
SETTINGS = pathlib.Path(os.environ.get('WW_PLAY_SETTINGS') or HERE / 'play_settings.json')
WEB = HERE / 'play_web'
# The proofreader's own copy of the script: tools/export_text.py writes it, tools/check.py
# checks it, tools/import_text.py carries it back into en/*.rkt. The cockpit only ever writes
# HERE -- never into en/, and never into the game (STATUS §32: one road into the scripts).
TEXT = pathlib.Path(os.environ.get('WW_TEXT') or HERE.parent / 'text')
EDIT_LOG = pathlib.Path(os.environ.get('WW_PLAY_EDITLOG') or HERE / 'play_edits.jsonl')
LOG = pathlib.Path(os.environ.get('WW_PLAY_LOG') or HERE / 'play.log')
PORT = int(os.environ.get('WW_PLAY_PORT') or 8778)
HOST = '127.0.0.1'
# ⚠️ Only the cockpit on the default port owns emu/play.pid. play_live_test.py runs a second
# cockpit on another port; it used to overwrite this file and delete it on exit, leaving the
# live cockpit without one (caught 2026-09-16 by play_watch.py). Others keep theirs next to
# their snapshots, and nobody deletes a pid file that is not theirs.
PIDFILE = HERE / 'play.pid' if PORT == 8778 else STATES / 'play.pid'

SLOTS = 4                  # quick-save slots shown in the cockpit
AUTO_KEEP = 3              # rolling emulator snapshots while in the game
AUTO_EVERY = 300.0         # s between them
KEEP_BACKUPS = 8           # copies of the disk image taken at startup
TELE_EVERY = 0.12          # s; memory is read in place, so this costs microseconds
IDENT_EVERY = 2.0          # s; state.identify() scans the slice (~120 ms), so not every tick
TEXT_EVERY = 0.3           # s between readings of the message window (only on a changed frame)
TEXT_RETRY = 1.0           # ...but read a standing screen again this often: the window may have
                           # been caught half-drawn, and a still picture sends no new frames
STALL_KILL = 20.0          # s without a heartbeat from a running emulator -> kill and restart
TRACE = bool(os.environ.get('WW_PLAY_TRACE'))   # log every input command and button press
FRAME_MAGIC = b'WWF1'
FRAME_HEAD = struct.Struct('<4sHHHHI')      # magic, width, height, pitch, reserved, seq

log = logging.getLogger('play')


def setup_logging():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    fh = logging.handlers.RotatingFileHandler(LOG, maxBytes=1 << 20, backupCount=2)
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stderr.isatty():             # started by hand: echo to the terminal too
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)


# ---- setup ---------------------------------------------------------------------------------
def prepare_system():
    """Private copy of emu/system, pointed at our disk."""
    if not SYSTEM.exists():
        shutil.copytree(HERE / 'system', SYSTEM)
    SAVE_DIR.mkdir(exist_ok=True)
    STATES.mkdir(exist_ok=True)
    cfg = SYSTEM / 'np2kai' / 'np2kai.cfg'
    if cfg.exists():
        lines = cfg.read_text().splitlines()
        lines = [f'HDD1FILE = {DISK}' if l.startswith('HDD1FILE') else l for l in lines]
        cfg.write_text('\n'.join(lines) + '\n')


def backup_disk():
    """The in-game saves live inside the image; keep a copy from every start."""
    BACKUPS.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    dst = BACKUPS / f'{DISK.stem}-{stamp}{DISK.suffix}'
    shutil.copy2(DISK, dst)
    old = sorted(BACKUPS.glob(f'{DISK.stem}-*{DISK.suffix}'))
    for f in old[:-KEEP_BACKUPS]:
        f.unlink()
    return dst


def other_writer():
    """Pid of a QA agent running on this same disk, or None."""
    try:
        pid = int((HERE / 'agent.pid').read_text().strip())
        cmd = pathlib.Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
    except (OSError, ValueError):
        return None
    if not any(c.endswith(b'agent.py') for c in cmd):
        return None
    try:
        env = pathlib.Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
        disk = next((e[8:].decode() for e in env if e.startswith(b'WW_DISK=')), None)
        launch = json.loads((HERE / 'agent.launch.json').read_text()).get('disk')
    except (OSError, ValueError):
        return pid
    mine = str(DISK)
    return pid if (disk and pathlib.Path(disk).resolve() == DISK) or launch == mine or not disk else None


def load_settings():
    # ⚠️ Sound OFF by default. A test cockpit started with a fresh settings file began playing
    # the game's music out loud on the owner's desktop (2026-09-16); nothing started by a script
    # should make noise on its own. The button turns it on.
    d = {'speed': 1, 'sound': False, 'volume': 0.7}
    try:
        d.update(json.loads(SETTINGS.read_text()))
    except (OSError, ValueError):
        pass
    if d.get('speed') not in play_core.SPEEDS:
        d['speed'] = 1
    return d


def save_settings(d):
    tmp = SETTINGS.with_suffix('.tmp')
    tmp.write_text(json.dumps(d))
    tmp.replace(SETTINGS)


def playing(scene_mem, stats, stack=None):
    """Are we inside the game right now, rather than on the title screen or in a menu?

    ⚠️ The sharp answer needs the scripts (`boot.in_game` over the resident stack), and a public
    copy of this repository has none -- that IS the translation. Gating the sensors on it made
    the cockpit show nothing at all there: no hero, no position, no map, and autobattle refused
    to start, although every one of those comes out of memory and never touches a script
    (caught 2026-09-16 by running the acceptance against the exported tree).

    Without scripts the game's own scene string plus a sane player block answer it well enough:
    on the title and in G_OVER the block holds a new game's values (level 0), and the scene is
    START1.MES.
    """
    if stack:
        try:
            import boot
            return boot.in_game(stack)
        except Exception:
            pass
    name = (scene_mem or '').upper()
    if not name or name.startswith(('START', 'PARA', 'G_OVER')):
        return False
    st = stats or {}
    hp, mx, lvl = st.get('hp'), st.get('hp_max'), st.get('level')
    return bool(mx) and lvl is not None and lvl > 0 and hp is not None and 0 <= hp <= mx


def rgb565_to_png(pix, w, h, pitch, path, scale=0.25):
    import numpy as np
    from PIL import Image
    a = np.frombuffer(pix, dtype=np.uint16)[:h * pitch // 2].reshape(h, pitch // 2)[:, :w]
    rgb = np.dstack([((a >> 11) & 31) << 3, ((a >> 5) & 63) << 2, (a & 31) << 3]).astype(np.uint8)
    im = Image.fromarray(rgb)
    if scale != 1:
        im = im.resize((int(w * scale), int(h * scale * 1.2)), Image.LANCZOS)
    tmp = pathlib.Path(path).with_suffix('.tmp.png')
    im.save(tmp)
    tmp.replace(path)


# ---- the emulator, supervised --------------------------------------------------------------
class Emulator:
    def __init__(self, hub):
        self.hub = hub
        self.ctx = mp.get_context('spawn')
        self.shm = shared_memory.SharedMemory(create=True, size=play_core.SHM_SIZE)
        self.proc = None
        self.cmd = None
        self.status = None
        self.frame_event = threading.Event()
        self.stat = {}
        self.state = 'starting'          # starting | booting | running | crashed | stopped
        self.trace = None
        self.mem_base = None
        self.mem_fd = None
        self.restarts = []
        self.last_heartbeat = time.time()
        self.last_frame_at = time.time()
        self.pending = {}
        self._rid = 0
        self._lock = threading.Lock()
        self.stopping = False

    def start(self):
        s = self.hub.settings
        cfg = {'shm': self.shm.name, 'disk': str(DISK), 'system': str(SYSTEM),
               'save': str(SAVE_DIR), 'speed': s['speed'], 'sound': s['sound'],
               'volume': s['volume'], 'latency': '120ms', 'trace': TRACE}
        self.cmd = self.ctx.Queue()
        self.status = self.ctx.Queue(maxsize=4096)
        self.mem_base = None
        self._close_mem()
        self.trace = None
        self.state = 'booting'
        self.proc = self.ctx.Process(target=play_core.main, args=(cfg, self.cmd, self.status),
                                     name='ww-core', daemon=True)
        self.proc.start()
        self.last_heartbeat = time.time()
        log.info('emulator started, pid %s, disk %s', self.proc.pid, DISK)
        threading.Thread(target=self._listen, args=(self.status, self.proc), daemon=True).start()
        self.hub.emu_changed()

    def send(self, msg):
        if self.cmd is not None and self.proc is not None and self.proc.is_alive():
            self.cmd.put(msg)
            return True
        return False

    def request(self, msg, timeout=15.0):
        """Send a command that answers (save/load) and wait for the answer."""
        with self._lock:
            self._rid += 1
            rid = self._rid
            ev = threading.Event()
            self.pending[rid] = [ev, None]
        if not self.send({**msg, 'id': rid}):
            self.pending.pop(rid, None)
            return None
        ev.wait(timeout)
        return self.pending.pop(rid, [None, None])[1]

    def _close_mem(self):
        if self.mem_fd is not None:
            try:
                os.close(self.mem_fd)
            except OSError:
                pass
        self.mem_fd = None

    def _open_mem(self, base):
        self._close_mem()
        self.mem_base = base
        if base is None:
            return
        try:
            # The parent may read its CHILD's memory even under Yama ptrace_scope=1.
            self.mem_fd = os.open(f'/proc/{self.proc.pid}/mem', os.O_RDONLY)
        except OSError as e:
            log.warning('cannot read emulator memory: %s', e)
            self.mem_fd = None

    def read_low(self):
        fd, base = self.mem_fd, self.mem_base
        if fd is None or base is None:
            return None
        try:
            low = os.pread(fd, play_core.LOW, base)
        except OSError:
            return None
        # ⚠️ A short or empty read is NOT data. While the emulator is restarting, pread returns
        # b'' -- and handing that on blew up the whole telemetry tick on the first unpack, which
        # silently took the map, the text panel and autobattle down with it (2026-09-16).
        return low if len(low) == play_core.LOW else None

    def _listen(self, status, proc):
        while True:
            try:
                m = status.get(timeout=0.5)
            except queue.Empty:
                if not proc.is_alive():
                    break
                continue
            except (EOFError, OSError):
                break
            t = m.get('t')
            self.last_heartbeat = time.time()
            if t == 'frame':
                self.last_frame_at = time.time()
                self.frame_event.set()
            elif t == 'stat':
                self.stat = m
                if self.state != 'running':
                    self.state = 'running'
                    self.hub.emu_changed()
            elif t == 'boot':
                if m.get('stage') == 'title':
                    self._open_mem(m.get('mem'))
                    self.state = 'running'
                    log.info('booted to title; memory %s', hex(m['mem']) if m.get('mem') else 'NOT located')
                self.hub.emu_changed()
            elif t == 'mem':
                self._open_mem(m.get('mem'))
            elif t in ('saved', 'loaded'):
                p = self.pending.get(m.get('id'))
                if p:
                    p[1] = m
                    p[0].set()
            elif t == 'crash':
                self.trace = m.get('trace')
                log.error('emulator exception:\n%s', self.trace)
            elif t == 'log':
                log.info('core: %s', m.get('msg'))
        proc.join(2)
        code = proc.exitcode
        self._close_mem()
        for p in list(self.pending.values()):
            p[0].set()
        if self.stopping or proc is not self.proc:
            return
        self.state = 'crashed'
        why = f'signal {-code}' if code is not None and code < 0 else f'exit code {code}'
        log.error('emulator died: %s', why)
        self.hub.event(f'Emulator stopped ({why}). Restarting to the title menu -- load your save.',
                       level='error')
        self.hub.emu_changed()
        now = time.time()
        self.restarts = [t for t in self.restarts if now - t < 300] + [now]
        if len(self.restarts) > 5:
            self.state = 'stopped'
            self.hub.event('Emulator keeps dying: stopped restarting. See emu/play.log.', level='error')
            self.hub.emu_changed()
            return
        time.sleep(1.0)
        self.start()

    def watchdog(self):
        """A live but silent emulator is hung: kill it, the listener restarts it."""
        while not self.stopping:
            time.sleep(2)
            p = self.proc
            if (p is not None and p.is_alive() and self.state == 'running'
                    and time.time() - self.last_heartbeat > STALL_KILL):
                log.error('emulator silent for %.0f s: killing it', time.time() - self.last_heartbeat)
                self.hub.event('Emulator stopped responding; restarting it.', level='error')
                p.kill()

    def stop(self):
        self.stopping = True
        p = self.proc
        if p is not None and p.is_alive():
            self.send({'c': 'quit'})
            p.join(5)
            if p.is_alive():
                p.terminate()
                p.join(2)
        self._close_mem()
        try:
            self.shm.close()
            self.shm.unlink()
        except FileNotFoundError:
            pass

    def latest_frame(self):
        """(payload, seq) of the current picture, read under the seqlock."""
        buf = self.shm.buf
        for _ in range(20):
            s1, w, h, pitch, _r = play_core.FRAME_HDR.unpack_from(buf, 0)
            if s1 & 1 or not w:
                time.sleep(0.001)
                continue
            n = h * pitch
            pix = bytes(buf[play_core.FRAME_OFF:play_core.FRAME_OFF + n])
            s2 = play_core.FRAME_HDR.unpack_from(buf, 0)[0]
            if s1 == s2:
                return pix, w, h, pitch, s1 // 2
        return None


# ---- the hub: everything the page sees ------------------------------------------------------
class Hub:
    def __init__(self):
        self.settings = load_settings()
        self.peers = set()
        self.plock = threading.Lock()
        self.emu = Emulator(self)
        self.tele = {}
        self.map = None
        self.events = []
        self.started = time.time()
        self._map_key = None
        self._tele_sent = None
        self._tele_at = 0.0
        self._last_auto = time.time()
        self._auto_i = 0
        self.atlas = None
        self._atlas_dirty = False
        self._atlas_saved = time.time()
        self._seen_sig = None
        self._cell = None
        self._steps = 0
        self._ident = {'at': 0.0, 'scene_mem': None, 'val': {}}
        self.grind = None          # the autobattle script while it runs
        self.grind_cfg = {**play_grind.DEFAULTS, **(self.settings.get('grind') or {})}
        self._grind_keys = []
        self._grind_at = 0.0
        self.text = None            # what the message window says + where that line lives
        self._text_seq = -1
        self._text_at = 0.0
        self._text_lines = None     # the previous reading: the window must settle before lookup

    # -- peers --
    def add(self, peer):
        with self.plock:
            self.peers.add(peer)
        peer.text(json.dumps(self.hello()))
        f = self.emu.latest_frame()
        if f:
            peer.frame(self._frame_payload(*f))

    def drop(self, peer):
        with self.plock:
            self.peers.discard(peer)
        peer.close()

    def broadcast(self, obj):
        s = json.dumps(obj, ensure_ascii=False)
        with self.plock:
            peers = list(self.peers)
        for p in peers:
            if p.alive:
                p.text(s)
            else:
                self.drop(p)

    def event(self, msg, level='info'):
        e = {'t': 'event', 'msg': msg, 'level': level, 'ts': time.time()}
        self.events = (self.events + [e])[-30:]
        self.broadcast(e)

    def emu_view(self):
        e = self.emu
        st = e.stat or {}
        return {'t': 'emu', 'state': e.state, 'fps': st.get('fps'), 'speed': st.get('speed'),
                'turbo': st.get('turbo'), 'paused': st.get('paused'), 'sound': st.get('sound'),
                'dropped': st.get('dropped'), 'queued': st.get('queued'),
                'aspect': st.get('aspect'), 'core_fps': st.get('core_fps'),
                'mem': e.mem_base is not None, 'restarts': len(e.restarts),
                'frame_age': round(time.time() - e.last_frame_at, 1),
                'grind': self.grind_view(),
                'uptime': round(time.time() - self.started), 'peers': len(self.peers),
                'pid': e.proc.pid if e.proc else None, 'disk': DISK.name,
                'settings': self.settings}

    def emu_changed(self):
        self.broadcast(self.emu_view())

    def hello(self):
        return {'t': 'hello', 'emu': self.emu_view(), 'tele': self.tele, 'map': self.map,
                'text': self.text,
                'slots': self.slots(), 'events': self.events[-5:], 'settings': self.settings}

    # -- frames --
    @staticmethod
    def _frame_payload(pix, w, h, pitch, seq):
        if pitch != w * 2:
            import numpy as np
            a = np.frombuffer(pix, dtype=np.uint16).reshape(h, pitch // 2)[:, :w]
            pix = np.ascontiguousarray(a).tobytes()
        return FRAME_HEAD.pack(FRAME_MAGIC, w, h, w * 2, 0, seq & 0xFFFFFFFF) + pix

    def frames(self):
        last = -1
        while True:
            self.emu.frame_event.wait(0.5)
            self.emu.frame_event.clear()
            with self.plock:
                peers = [p for p in self.peers if p.alive]
            if not peers:
                continue
            f = self.emu.latest_frame()
            if not f or f[4] == last:
                continue
            last = f[4]
            payload = self._frame_payload(*f)
            for p in peers:
                p.frame(payload)

    # -- telemetry and map --
    def _identify(self, low, scene_mem):
        import state
        now = time.time()
        ident = self._ident
        if ident['val'] and scene_mem == ident['scene_mem'] and now - ident['at'] < IDENT_EVERY:
            return ident['val']
        try:
            val = state.identify(low)
        except Exception:
            val = {}
        ident.update(at=now, scene_mem=scene_mem, val=val)
        return val

    def read_tele(self, low):
        import state
        out = {}
        scene_mem = state.scene_name(low)
        ident = self._identify(low, scene_mem)
        stack = ident.get('stack') or []
        for name, fn in (('stats', state.stats), ('items', state.items),
                         ('equipment', state.equipment), ('names', state.names)):
            try:
                out[name] = fn(low)
            except Exception:
                out[name] = None
        in_play = playing(scene_mem, out['stats'], stack)
        out['in_play'] = in_play
        out['scene'] = scene_mem
        # ⚠️ The atlas key: identify() names the resident script, but it needs the translation
        # sources, which a public copy does not have. The game's own scene string works as a key
        # just as well -- and it is read straight out of memory.
        out['room'] = ident.get('scene') or scene_mem
        try:
            out['hero'] = state.hero(low, out['room'])
        except Exception:
            out['hero'] = None
        cx, cy = struct.unpack_from('<HH', low, play_core.CURSOR)
        out['cursor'] = [cx, cy]
        if in_play:
            w = state.where(low)
            out['pos'] = {'x': w['x'], 'y': w['y'], 'facing': w['facing']} if w else None
        else:
            # ⚠️ Outside the game the player block holds the values of a NEW game (PARA.MES),
            # not yours: showing them would be a lie (emu/state.py stats()).
            for name in ('stats', 'items', 'equipment', 'names', 'hero'):
                out[name] = None
        return out

    def _atlas(self):
        if self.atlas is None:
            import atlas
            self.atlas = atlas.load()
        return self.atlas

    def update_map(self, low, tele):
        import atlas
        import state
        if not tele.get('in_play') or not tele.get('room'):
            return
        # Only dungeon floors have a map. In the inn (YADO) the map block holds one leftover
        # cell, which drew as a lone giant arrow and would have seeded a "floor" into the atlas.
        if not str(tele.get('scene') or '').startswith('FLOOR'):
            # ⚠️ The key has to carry the scene. It used to be the bare string 'none', so moving
            # from one roomed scene to another never looked like a change, and the panel went on
            # naming the scene you had LEFT -- "No map in Para" with the header reading Yado
            # (2026-09-16).
            key = ('none', tele.get('scene'), tele.get('room'))
            if self._map_key != key:
                self._map_key = key
                self.map = {'scene': tele['room'], 'floor': tele.get('scene'), 'cells': {}}
                self.broadcast({'t': 'map', **self.map})
            return
        try:
            fm = state.floormap(low)
        except Exception:
            fm = None
        at = self._atlas()
        scene = tele['room']
        if fm and fm['sig'] != self._seen_sig:
            atlas.seed(at, scene, fm)
            self._seen_sig = fm['sig']
            self._atlas_dirty = True
        pos = tele.get('pos') or {}
        cell = (scene, pos.get('x'), pos.get('y'))
        if pos and cell != self._cell:
            self._cell = cell
            self._steps += 1
            atlas.visit(at, scene, pos['x'], pos['y'], self._steps)
            self._atlas_dirty = True
        key = (scene, pos.get('x'), pos.get('y'), pos.get('facing'), self._seen_sig)
        if key != self._map_key:
            self._map_key = key
            m = atlas.as_json(at, scene, pos.get('x'), pos.get('y'), pos.get('facing'))
            m = {k: m[k] for k in ('scene', 'here', 'facing', 'cells', 'edges', 'dist', 'monsters')}
            m['floor'] = tele.get('scene')
            self.map = m
            self.broadcast({'t': 'map', **m})
        if self._atlas_dirty and time.time() - self._atlas_saved > 30:
            atlas.save(at)
            self._atlas_dirty = False
            self._atlas_saved = time.time()

    # -- autobattle ------------------------------------------------------------------------
    def grind_view(self):
        g = self.grind
        return {'on': g is not None, 'cfg': self.grind_cfg,
                'fights': g.fights if g else 0, 'steps': g.steps if g else 0,
                'stopped': self.grind_cfg.get('stopped')}

    def grind_set(self, on, **cfg):
        for k in ('hp_floor', 'span'):
            if k in cfg and cfg[k] is not None:
                self.grind_cfg[k] = int(cfg[k])
        if on:
            self.grind_cfg.pop('stopped', None)
            # ⚠️ The avoid list is an observation of THIS run, not a setting. Kept across runs it
            # poisoned the floor: cells wrongly marked while autobattle was still buggy stayed
            # forbidden forever, and the hero ended up pacing between two cells (2026-09-16).
            self.grind_cfg['avoid'] = []
            self.grind = play_grind.Grind(self.grind_cfg)
            self.event(f'Autobattle on: walking, fighting, stops below '
                       f'{self.grind_cfg["hp_floor"]}% HP.')
        else:
            self.grind = None
            self._grind_keys = []
            self.event('Autobattle off.')
        self.settings['grind'] = {k: v for k, v in self.grind_cfg.items() if k != 'stopped'}
        save_settings(self.settings)
        self.emu_changed()

    def grind_stop(self, why):
        """⚠️ Back to the HUMAN, never to something else, and the reason is on the screen."""
        g, self.grind = self.grind, None
        self._grind_keys = []
        if g:
            self.grind_cfg['avoid'] = [list(c) for c in sorted(g.avoid)]
        self.grind_cfg['stopped'] = why
        self.settings['grind'] = {k: v for k, v in self.grind_cfg.items()
                                  if k not in ('stopped', 'avoid')}
        save_settings(self.settings)
        self.event(f'Autobattle stopped: {why}', level='error')
        self.emu_changed()

    def grind_step(self, low, tele):
        if self.grind is None:
            return
        import state
        now = time.time()
        if self._grind_keys:
            if now >= self._grind_at:
                key, frames = self._grind_keys.pop(0)
                if TRACE:
                    log.info('grind sends %s for %s frames', key, frames)
                self.emu.send({'c': 'tap', 'k': key, 'frames': frames})
                self._grind_at = now + self.grind.key_gap(frames)
            return
        if not tele.get('in_play'):
            return
        pos, st = tele.get('pos') or {}, tele.get('stats') or {}
        sides = {}
        if pos.get('x') is not None:
            try:
                fm = state.floormap(low)
                if fm:
                    sides = {s: state.map_side(fm, pos['x'], pos['y'], s) for s in range(4)}
            except Exception:
                sides = {}
        reading = {'scene': tele.get('scene'), 'x': pos.get('x'), 'y': pos.get('y'),
                   'facing': pos.get('facing'), 'hp': st.get('hp'), 'hp_max': st.get('hp_max'),
                   'sides': sides, 'cursor': tele.get('cursor'),
                   'frame_moving': now - self.emu.last_frame_at < 0.7,
                   # how fast the game is running: the script's waits are wall-clock, the
                   # game's animations are frames, and at x4 they are over four times sooner
                   'speed': 0 if (self.emu.stat or {}).get('turbo') else self.settings['speed']}
        act = self.grind.step(reading, now=now)
        if TRACE and act:
            log.info('grind at (%s,%s) facing %s sides %s -> %s',
                     reading['x'], reading['y'], reading['facing'], sides, act)
        if act.get('stop'):
            self.grind_stop(act['stop'])
        elif act.get('keys'):
            self._grind_keys = list(act['keys'])
            self._grind_at = now

    def update_text(self, names):
        """The message window, read off the frame, plus where that line lives in `en/*.rkt`.

        ⚠️ Read from the PICTURE, not from memory: the message text is not in the PC-98 text
        layer (emu/textbox.py -- 0xA0000 in a snapshot gave garbage on all six tries), and the
        game has its own font, so the glyphs come from templates learned by emu/learn_font.py.
        Both steps are the ones the old dashboard used; here they run in the cockpit process,
        where they cannot slow a frame down, and only when the picture actually changed.
        """
        now = time.time()
        if now - self._text_at < TEXT_EVERY:
            return
        f = self.emu.latest_frame()
        # ⚠️ Not only on a NEW frame. A message window is drawn once and then the picture stops
        # changing, so a single reading that caught the frame mid-draw left the panel empty for
        # as long as the screen stood still -- which made the running acceptance flap between
        # "the line is read" and "no candidate" with no code change (2026-09-16).
        if not f or (f[4] == self._text_seq and now - self._text_at < TEXT_RETRY):
            return
        self._text_at, self._text_seq = now, f[4]
        try:
            import numpy as np
            from PIL import Image
            import textbox
            import textsrc
        except ImportError:
            return
        pix, w, h, pitch, _seq = f
        a = np.frombuffer(pix, dtype=np.uint16)[:h * pitch // 2].reshape(h, pitch // 2)[:, :w]
        rgb = np.dstack([((a >> 11) & 31) << 3, ((a >> 5) & 63) << 2, (a & 31) << 3]).astype('uint8')
        img = Image.fromarray(rgb)
        try:
            lines = textbox.lines(img)
        except Exception as e:
            log.warning('cannot read the message window: %s', e)
            return
        out = None
        # ⚠️ The game TYPES the message out, so a reading can catch it half-written ("...before
        # yo"), and looking that up finds nothing. Show the text at once -- it is what is on the
        # screen -- but only look for its source once the window has read the same twice.
        settled = lines == self._text_lines
        self._text_lines = lines
        if any(l.strip() for l in lines) and not settled:
            out = {'lines': lines, 'settling': True, 'candidates': [], 'more': 0,
                   'sources': None, 'scene': (self.tele or {}).get('room')}
        elif any(l.strip() for l in lines):
            who = tuple(n for n in (names or {}).values() if n) or ('Astral', 'Pollux')
            scene = (self.tele or {}).get('room') or (self.tele or {}).get('scene')
            try:
                found = textsrc.candidates(lines, names=who, prefer=scene)
            except Exception as e:
                log.warning('cannot locate the line: %s', e)
                found = []
            try:
                kind = textsrc.sources()
            except Exception:
                kind = None
            out = {'lines': lines, 'scene': scene, 'sources': kind,
                   'candidates': [self.describe(c) for c in found[:8]],
                   'more': max(0, len(found) - 8)}
        if out != self.text:
            self.text = out
            self.broadcast({'t': 'text', 'text': out})

    # -- the proofreader's line ------------------------------------------------------------
    def describe(self, cand):
        """A candidate plus the state of its entry in the proofreader's JSON.

        `editable` is the honest answer to "may this be rewritten from here": the screen text
        must match this form exactly, the entry must exist, and its fingerprint must still
        match the source -- otherwise tools/import_text.py would refuse the edit later anyway.
        """
        out = dict(cand)
        jf = TEXT / f'{cand["file"]}.json'
        out['json'] = jf.name if jf.exists() else None
        row = None
        if jf.exists():
            try:
                row = next((r for r in json.loads(jf.read_text(encoding='utf-8'))
                            if r.get('id') == cand['id']), None)
            except (OSError, ValueError) as e:
                out['why'] = f'cannot read {jf.name}: {e}'
        if row is None:
            out.setdefault('why', 'no entry in the proofreader copy'
                           if jf.exists() else f'no {cand["file"]}.json here')
            out['editable'] = False
            return out
        out['en'] = row['en']
        out['edited'] = self._fingerprint(row['en']) != row['was']
        # ⚠️ Only when the line was identified against the SOURCES is there something to
        # fingerprint. In pack mode the JSON is the only text there is, and its `was` belongs
        # to the export it came from -- comparing it against itself would refuse every edit.
        fresh = cand.get('kind') == 'pack' or self._fingerprint(cand['form']) == row['was']
        out['fresh'] = fresh
        if not fresh:
            out['why'] = 'the source line changed after the export: re-export text/ first'
        out['editable'] = bool(cand['exact'] and fresh)
        if cand['exact'] and not out.get('why') and not out['editable']:
            out['why'] = 'not editable'
        return out

    @staticmethod
    def _fingerprint(s):
        import hashlib
        return hashlib.blake2b(s.encode(), digest_size=4).hexdigest()

    def dry_check(self, ident, new_text):
        """What the checker would say about this text -- writing nothing.

        The panel asks while the proofreader types, so a change that will be refused (the
        speaker tag, a lost `{0}`, a line too wide for the window) shows up before they reach
        for Save. ⚠️ The RULES are not restated here: this calls the same `check.problems` the
        write calls, because two copies of the rules drift apart and the quiet one wins.
        """
        # ⚠️ Deliberately NOT limited to the line on screen right now, the way `edit_line` is:
        # this writes nothing, and the panel keeps checking while it holds a line the game has
        # walked away from. The id is untrusted, so it is taken apart rather than trusted.
        ident = str(ident or '')
        name, _, ordinal = ident.partition('#')
        if (not ordinal.isdigit() or not name.endswith('.MES')
                or '/' in name or '\\' in name or '..' in name):
            return {'id': ident, 'problems': []}
        jf = TEXT / f'{name}.json'
        try:
            rows = json.loads(jf.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {'id': ident, 'problems': []}
        row = next((r for r in rows if r.get('id') == ident), None)
        new_text = str(new_text).replace('\r\n', '\n').rstrip('\n')
        if row is None or new_text == row['en']:
            return {'id': ident, 'problems': []}
        try:
            sys.path.insert(0, str(HERE.parent / 'tools'))
            import check
            return {'id': ident, 'problems': check.problems({**row, 'en': new_text})}
        except Exception as e:
            return {'id': ident, 'problems': [f'the checker failed: {e}']}

    def edit_line(self, ident, new_text, peer=None):
        """Rewrite the proofreader's entry for the line on screen -- every copy of it.

        ⚠️ 12% of this game's screen lines are rendered by more than one entry, and half of
        those sit inside a single file, so neither the scene nor a picker can say which
        occurrence the game is showing. When all of them hold the SAME text that question does
        not need answering -- the correction belongs in all of them, and all of them are
        written. When they hold different text it refuses: one correction must not be spread
        over entries that are not the same. (Before 2026-09-16 the whole ambiguous case was
        refused with "pick one first", which was a lie: the refusal did not look at the choice,
        so picking never helped and 12% of the game was unreachable from here.)
        """
        offered = {c['id']: c for c in ((self.text or {}).get('candidates') or [])
                   if c.get('editable')}
        cand = offered.get(ident)
        if cand is None:
            return {'ok': False, 'why': 'that line is not the one on screen right now'}
        group = [c for c in offered.values() if c.get('exact')] or [cand]
        if not any(c['id'] == ident for c in group):
            group = [cand]
        new_text = str(new_text).replace('\r\n', '\n').rstrip('\n')

        loaded, targets = {}, []
        for c in group:
            jf = TEXT / f'{c["file"]}.json'
            if jf not in loaded:
                try:
                    loaded[jf] = json.loads(jf.read_text(encoding='utf-8'))
                except (OSError, ValueError) as e:
                    return {'ok': False, 'why': f'cannot read {jf.name}: {e}'}
            row = next((r for r in loaded[jf] if r.get('id') == c['id']), None)
            if row is None:
                return {'ok': False, 'why': f'{c["id"]} is not in {jf.name}'}
            if c.get('kind') != 'pack' and self._fingerprint(c['form']) != row['was']:
                return {'ok': False, 'why': 'the source line changed after the export'}
            targets.append((c, jf, row))
        if len({row['en'] for _, _, row in targets}) > 1:
            where = ', '.join(sorted(f'{c["file"]}#{c["id"].split("#")[-1]}' for c, _, _ in targets))
            return {'ok': False, 'why': f'this line is in {len(targets)} places that hold '
                                        f'different text, so the cockpit cannot tell which one '
                                        f'is on screen: {where}'}
        row0 = targets[0][2]
        if new_text == row0['en']:
            return {'ok': False, 'why': 'nothing changed'}
        try:
            sys.path.insert(0, str(HERE.parent / 'tools'))
            import check
            problems = check.problems({**row0, 'en': new_text})
        except Exception as e:
            return {'ok': False, 'why': f'the checker failed: {e}'}
        if problems:
            return {'ok': False, 'why': 'the game would refuse this line', 'problems': problems}

        before = row0['en']
        for _c, _jf, row in targets:
            row['en'] = new_text
        for jf, rows in loaded.items():
            tmp = jf.with_suffix('.tmp.json')
            tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
            tmp.replace(jf)
        with open(EDIT_LOG, 'a', encoding='utf-8') as f:
            for c, jf, _row in targets:
                f.write(json.dumps({'ts': time.time(), 'id': c['id'], 'file': jf.name,
                                    'was': before, 'now': new_text}, ensure_ascii=False) + '\n')
        ids = [c['id'] for c, _, _ in targets]
        log.info('proofread edit %s in %s', ids, sorted(j.name for j in loaded))
        self.event(f'Saved {ident} to {targets[0][1].name}'
                   + (f' and {len(ids) - 1} more place(s) holding the same line' if len(ids) > 1
                      else '')
                   + '. It reaches the game with tools/check.py + tools/import_text.py.')
        self._text_seq = -1               # re-read the window so the panel shows the new state
        return {'ok': True, 'id': ident, 'file': targets[0][1].name, 'en': new_text,
                'ids': ids, 'places': len(ids)}

    def telemetry(self):
        last_emu = 0.0
        while True:
            time.sleep(TELE_EVERY)
            now = time.time()
            if now - last_emu >= 0.5:
                last_emu = now
                self.broadcast(self.emu_view())
            low = self.emu.read_low()
            if low is None:
                continue
            # ⚠️ Each part on its own: one of them failing must not take the others with it.
            # They used to share a try/except, and a single unpack error on a torn memory read
            # stopped the map, the message window and autobattle for the whole tick -- for long
            # enough that the running acceptance reported them all dead (2026-09-16).
            try:
                tele = self.read_tele(low)
            except Exception:
                log.exception('telemetry: reading the game failed')
                continue
            for what, fn in (('map', lambda: self.update_map(low, tele)),
                             ('message window', lambda: self.update_text(tele.get('names'))),
                             ('autobattle', lambda: self.grind_step(low, tele))):
                try:
                    fn()
                except Exception:
                    log.exception('telemetry: %s failed', what)
            if tele != self._tele_sent or now - self._tele_at > 2:
                self._tele_sent = tele
                self._tele_at = now
                self.tele = tele
                self.broadcast({'t': 'tele', **tele})
            if (tele.get('in_play') and now - self._last_auto > AUTO_EVERY
                    and not (self.emu.stat or {}).get('paused')):
                self._last_auto = now
                threading.Thread(target=self.autosave, daemon=True).start()

    # -- save slots --
    def _slot_paths(self, name):
        return STATES / f'{name}.state', STATES / f'{name}.png', STATES / f'{name}.json'

    def slots(self):
        out = []
        for name in [f'slot{i}' for i in range(1, SLOTS + 1)] + [f'auto{i}' for i in range(AUTO_KEEP)]:
            st, png, meta = self._slot_paths(name)
            info = {'name': name, 'used': st.exists()}
            if st.exists():
                try:
                    info.update(json.loads(meta.read_text()))
                except (OSError, ValueError):
                    info['time'] = st.stat().st_mtime
                info['thumb'] = f'/states/{name}.png?{int(st.stat().st_mtime)}' if png.exists() else None
            out.append(info)
        return out

    def save_slot(self, name):
        st, png, meta = self._slot_paths(name)
        r = self.emu.request({'c': 'save', 'path': str(st)})
        if not r or not r.get('ok'):
            self.event(f'Could not save {name}.', level='error')
            return False
        f = self.emu.latest_frame()
        if f:
            try:
                rgb565_to_png(f[0], f[1], f[2], f[3], png)
            except Exception as e:
                log.warning('thumbnail failed: %s', e)
        t = self.tele or {}
        s = t.get('stats') or {}
        meta.write_text(json.dumps({'time': time.time(), 'scene': t.get('scene'),
                                    'level': s.get('level'), 'hp': s.get('hp'),
                                    'hp_max': s.get('hp_max'), 'in_play': t.get('in_play')}))
        self.broadcast({'t': 'slots', 'slots': self.slots()})
        return True

    def load_slot(self, name):
        st, _, _ = self._slot_paths(name)
        if not st.exists():
            self.event(f'{name} is empty.', level='error')
            return False
        r = self.emu.request({'c': 'load', 'path': str(st)})
        ok = bool(r and r.get('ok'))
        self.event(f'Loaded {name}.' if ok else f'Could not load {name}.',
                   level='info' if ok else 'error')
        self._cell = None
        return ok

    def autosave(self):
        name = f'auto{self._auto_i % AUTO_KEEP}'
        self._auto_i += 1
        if self.save_slot(name):
            log.info('autosaved %s', name)

    # -- commands from the page --
    # ⚠️ 'tap' belongs here too: a press measured in FRAMES is what scripts and probes use, and
    # while it was missing every tap a test sent was dropped in silence -- a whole calibration
    # ("a step needs 68 frames") was measured against presses that never happened (2026-09-16).
    FORWARD = {'key', 'tap', 'release_all', 'aim', 'move', 'btn', 'turbo', 'pause'}

    def command(self, m):
        c = m.get('c')
        if TRACE and c in ('aim', 'btn', 'key', 'move'):
            log.info('input %s', json.dumps(m))
        # the human touching the controls takes the game back from the script, at once
        if c in ('key', 'btn', 'aim', 'move') and self.grind is not None:
            self.grind_stop('you took the controls')
        if c == 'grind':
            return self.grind_set(bool(m.get('on')), hp_floor=m.get('hp_floor'), span=m.get('span'))
        if c in self.FORWARD:
            self.emu.send(m)
        elif c == 'speed':
            if m.get('v') in play_core.SPEEDS:
                self.settings['speed'] = m['v']
                save_settings(self.settings)
                self.emu.send(m)
        elif c == 'sound':
            if 'on' in m:
                self.settings['sound'] = bool(m['on'])
            if 'vol' in m:
                self.settings['volume'] = max(0.0, min(1.0, float(m['vol'])))
            save_settings(self.settings)
            self.emu.send({'c': 'sound', 'on': self.settings['sound'], 'vol': self.settings['volume']})
        elif c == 'slot_save' and m.get('name', '').startswith('slot'):
            threading.Thread(target=self.save_slot, args=(m['name'],), daemon=True).start()
        elif c == 'slot_load' and str(m.get('name', ''))[:4] in ('slot', 'auto'):
            threading.Thread(target=self.load_slot, args=(m['name'],), daemon=True).start()
        elif c == 'text_edit':
            r = self.edit_line(m.get('id'), m.get('en', ''))
            self.broadcast({'t': 'text_edit', **r})
        elif c == 'text_check':
            self.broadcast({'t': 'text_check', **self.dry_check(m.get('id'), m.get('en', ''))})
        elif c == 'reboot':
            self.event('Rebooting to the title menu.')
            self.emu.send({'c': 'reboot'})
        elif c == 'restart_emulator':
            p = self.emu.proc
            if p is not None and p.is_alive():
                self.event('Restarting the emulator.')
                p.kill()
            elif self.emu.state == 'stopped':
                self.emu.restarts = []
                self.emu.start()
        self.emu_changed() if c in ('speed', 'sound', 'pause') else None


# ---- HTTP + WebSocket ------------------------------------------------------------------------
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
         '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml',
         '.json': 'application/json', '.ico': 'image/x-icon', '.woff2': 'font/woff2'}


def make_handler(hub):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, ctype, body, cache=False):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'max-age=3600' if cache else 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def _file(self, root, rel):
            p = (root / rel).resolve()
            if root.resolve() not in p.parents or not p.is_file():
                return self._send(404, 'text/plain', b'not found')
            self._send(200, TYPES.get(p.suffix, 'application/octet-stream'), p.read_bytes())

        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if path == '/ws':
                return self._ws()
            if path == '/':
                return self._file(WEB, 'index.html')
            if path == '/state.json':
                body = json.dumps({'emu': hub.emu_view(), 'tele': hub.tele,
                                   'map_scene': (hub.map or {}).get('scene'),
                                   'events': hub.events[-10:],
                                   'peers': [{'sent': p.sent_frames, 'dropped': p.dropped_frames}
                                             for p in list(hub.peers)]}, ensure_ascii=False)
                return self._send(200, 'application/json', body.encode())
            if path.startswith('/states/'):
                return self._file(STATES, path[len('/states/'):])
            return self._file(WEB, path.lstrip('/'))

        def _ws(self):
            key = self.headers.get('Sec-WebSocket-Key')
            if not key or 'websocket' not in self.headers.get('Upgrade', '').lower():
                return self._send(400, 'text/plain', b'expected a websocket')
            self.send_response(101, 'Switching Protocols')
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', wsock.accept_key(key))
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            peer = wsock.Peer(self.connection)
            hub.add(peer)
            try:
                while peer.alive:
                    op, data = wsock.read_message(self.rfile, on_ping=peer.pong)
                    if op == wsock.CLOSE:
                        break
                    if op != wsock.TEXT:
                        continue
                    try:
                        m = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(m, dict):
                        hub.command(m)
            except (EOFError, OSError, wsock.ProtocolError):
                pass
            finally:
                # the tab went away with keys still held: let go of them
                hub.command({'c': 'release_all'})
                hub.drop(peer)

    return Handler


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    setup_logging()
    pid = other_writer()
    if pid:
        sys.exit(f'The QA agent (pid {pid}) is running on {DISK.name}. Two emulators on one disk '
                 f'image lose saves: stop it first (kill {pid}).')
    try:
        server = Server((HOST, PORT), None)
    except OSError as e:
        sys.exit(f'port {PORT} is busy ({e}); is the cockpit already running?')
    prepare_system()
    dst = backup_disk()
    log.info('disk backed up to %s', dst)
    hub = Hub()
    server.RequestHandlerClass = make_handler(hub)
    hub.emu.start()
    for fn in (hub.frames, hub.telemetry, hub.emu.watchdog):
        threading.Thread(target=fn, daemon=True).start()

    def stop(*_):
        log.info('stopping')
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    PIDFILE.write_text(str(os.getpid()))
    log.info('cockpit on http://%s:%d/', HOST, PORT)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        hub.emu.stop()
        if hub.atlas is not None and hub._atlas_dirty:
            import atlas
            atlas.save(hub.atlas)
        try:
            if PIDFILE.read_text().strip() == str(os.getpid()):
                PIDFILE.unlink()
        except OSError:
            pass
        log.info('stopped')


if __name__ == '__main__':
    main()
