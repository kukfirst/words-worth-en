#!/usr/bin/env python3
"""Acceptance for the WHOLE cockpit, by running it: every feature, one command, one verdict.

    emu/.venv/bin/python emu/play_accept.py                     # this tree
    emu/.venv/bin/python emu/play_accept.py --tree /path/copy   # e.g. the public export
    emu/.venv/bin/python emu/play_accept.py --skip slow         # without the 30 s stall check

Runs a cockpit of its own: a private copy of the disk image, its own system directory, states,
settings, proofreading copy and port. It never touches a running game.

Why by running: the parts that break are the ones between the parts -- a file missing from the
export, a command the page sends and the server does not answer, a snapshot that saves but does
not load, an emulator that dies and never comes back. Unit tests cannot see any of that.
"""
import argparse
import json
import os
import pathlib
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from play_live_test import Client                                    # noqa: E402

LOAD1 = (372, 82)        # "Load 1" on the title menu -- the owner's save (the inn)
LOAD2 = (372, 107)       # "Load 2" -- a dungeon floor, for the map and autobattle
BAD_LINE = '[Innkeeper]: Good morning~ \\ nothing can draw this'


class Run:
    def __init__(self, tree, disk, keep=False, system=None):
        self.tree = pathlib.Path(tree).resolve()
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ww-accept.'))
        self.keep = keep
        self.results = []
        self.before = self.footprint()
        self.disk = self.tmp / 'test.hdi'
        shutil.copy2(disk, self.disk)
        # ⚠️ The BIOS and fonts are NOT part of the public copy (nobody may redistribute them),
        # so a tree fresh out of the export has no system/ and this used to die with a bare
        # FileNotFoundError. Whoever runs it brings their own, as the cockpit itself requires.
        src = pathlib.Path(system) if system else self.tree / 'system'
        if not (src / 'np2kai').is_dir():
            sys.exit(f'no np2kai BIOS in {src}. Point --system at a directory that has '
                     f'np2kai/bios.rom, font.rom and the rest (the cockpit needs the same).')
        shutil.copytree(src, self.tmp / 'system')
        for name in ('text',):
            src = self.tree.parent / name
            if src.is_dir():
                shutil.copytree(src, self.tmp / name)
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            self.port = s.getsockname()[1]
        env = {**os.environ, 'WW_DISK': str(self.disk), 'WW_SYSTEM': str(self.tmp / 'system'),
               'WW_SAVE': str(self.tmp / 'save'), 'WW_PLAY_STATES': str(self.tmp / 'states'),
               'WW_PLAY_PORT': str(self.port), 'WW_TEXT': str(self.tmp / 'text'),
               'WW_PLAY_SETTINGS': str(self.tmp / 'settings.json'),
               # ⚠️ Everything else the cockpit writes next to its code. Before 2026-09-16 these
               # three were missing: every run left a fake edit in the tree's journal, a pack
               # index built from the throwaway text/, and its own walk in place of the atlas.
               'WW_PLAY_EDITLOG': str(self.tmp / 'play_edits.jsonl'),
               'WW_PACK_INDEX': str(self.tmp / 'text_index_pack.json'),
               'WW_ATLAS': str(self.tmp / 'atlas.json'),
               # ⚠️ Its own log, with input and decisions traced: when a check fails, the run
               # must hand over the evidence itself. Twice already a failure here cost a
               # separate hand-driven probe to find out what the cockpit had actually done.
               'WW_PLAY_LOG': str(self.tmp / 'play.log'), 'WW_PLAY_TRACE': '1'}
        # never make noise on someone's desktop: the acceptance run is silent from the start
        (self.tmp / 'settings.json').write_text(json.dumps({'speed': 1, 'sound': False}))
        self.proc = subprocess.Popen([sys.executable, str(self.tree / 'play.py')], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.c = None

    # -- plumbing --------------------------------------------------------------------------
    def footprint(self):
        """Every file of the tree under test (emu/ and the text/ beside it) with its mtime."""
        out = {}
        for root in (self.tree, self.tree.parent / 'text'):
            for f in root.rglob('*') if root.is_dir() else ():
                if f.is_file() and '__pycache__' not in f.parts and '.git' not in f.parts:
                    out[str(f.relative_to(self.tree.parent))] = f.stat().st_mtime_ns
        return out

    def check(self, name, ok, detail=''):
        self.results.append((name, bool(ok)))
        print(f'  {"✅" if ok else "❌"} {name}' + (f' -- {detail}' if detail else ''), flush=True)
        return ok

    def why(self, pattern, n=12):
        """The cockpit's own words about what it just did -- printed when a check fails."""
        try:
            lines = (self.tmp / 'play.log').read_text(errors='replace').splitlines()
        except OSError:
            return
        import re
        hits = [l for l in lines if re.search(pattern, l)][-n:]
        for l in hits:
            print(f'      | {l[:150]}', flush=True)

    def screen(self, name):
        """Save what is on the screen right now, so a failure can be looked at."""
        data = self.c.last_frame if self.c else None
        if not data:
            return None
        import numpy as np
        from PIL import Image
        w, h = struct.unpack_from('<HH', data, 4)
        a = np.frombuffer(data, dtype=np.uint16, count=w * h, offset=16).reshape(h, w)
        rgb = np.dstack([((a >> 11) & 31) << 3, ((a >> 5) & 63) << 2, (a & 31) << 3]).astype('uint8')
        out = self.tmp / f'{name}.png'
        Image.fromarray(rgb).save(out)
        self.keep = True                       # something to look at: keep the directory
        return out

    def url(self, path='/state.json'):
        return f'http://127.0.0.1:{self.port}{path}'

    def get(self, path, raw=False):
        body = urllib.request.urlopen(self.url(path), timeout=5).read()
        return body if raw else json.loads(body)

    def wait_running(self, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            try:
                d = self.get('/state.json')
                if d['emu']['state'] == 'running' and d['emu']['mem']:
                    return d
            except OSError:
                pass
            time.sleep(0.5)
        return None

    def title(self):
        self.c.send({'c': 'reboot'})
        return self.c.pump(40, until=lambda m: m.get('t') == 'tele' and m.get('scene') == 'START1.MES')

    def load(self, where):
        """Click a slot on the title menu the way a browser does: aim and click at once."""
        self.c.send({'c': 'aim', 'x': 600, 'y': 300})
        self.c.pump(2)
        self.c.send({'c': 'aim', 'x': where[0], 'y': where[1]})
        self.c.send({'c': 'btn', 'b': 'left', 'd': 1})
        self.c.send({'c': 'btn', 'b': 'left', 'd': 0})
        return self.c.pump(30, until=lambda m: m.get('t') == 'tele' and m.get('in_play'))

    def stop(self):
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        if not self.keep:
            shutil.rmtree(self.tmp, ignore_errors=True)

    # -- the checks ------------------------------------------------------------------------
    def answers(self, timeout=90):
        """Wait for the cockpit to come up at all -- the emulator boots first."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                self.get('/state.json')
                return self.check('the cockpit comes up and answers', True,
                                  f'{time.time() - (end - timeout):.1f} s')
            except OSError:
                if self.proc.poll() is not None:
                    break
                time.sleep(0.5)
        return self.check('the cockpit comes up and answers', False,
                          f'exit code {self.proc.poll()}')

    def serves_the_page(self):
        page = self.get('/', raw=True).decode()
        files = {p: len(self.get('/' + p, raw=True)) for p in ('play.js', 'crt.js', 'play.css')}
        ok = all(n > 500 for n in files.values()) and 'play.js' in page and '<canvas' in page
        self.check('the page and its files are served', ok, f'{files}')

    def boots(self):
        d = self.wait_running()
        self.check('boots to the title menu and finds the game memory in it', bool(d),
                   f'{d["emu"]["fps"]} fps' if d else 'never came up')
        return d

    def talks(self):
        self.c = Client(self.port)
        hello = self.c.pump(10, until=lambda m: m.get('t') == 'hello')
        self.c.send({'c': 'sound', 'on': False})
        self.c.pump(3)
        shape = self.c.frame_shape
        self.check('the socket delivers a hello and real frames',
                   bool(hello) and shape == (b'WWF1', 640, 400, 640 * 400 * 2),
                   f'{self.c.frames} frames, {shape and shape[1:3]}')

    def paces(self):
        got = {}
        for speed, want in ((1, 56.4), (2, 112.8), (4, 225.6)):
            self.c.send({'c': 'speed', 'v': speed})
            self.c.pump(2.5)
            m = self.c.pump(4, until=lambda m: m.get('t') == 'emu' and m.get('speed') == speed
                            and m.get('fps'))
            got[speed] = m and m['fps']
            time.sleep(0.2)
        self.c.send({'c': 'turbo', 'd': True})
        self.c.pump(2)
        m = self.c.pump(4, until=lambda m: m.get('t') == 'emu' and m.get('turbo') and m.get('fps'))
        turbo = m and m['fps']
        self.c.send({'c': 'turbo', 'd': False})
        self.c.send({'c': 'speed', 'v': 1})
        self.c.pump(2)
        ok = (got.get(1) and abs(got[1] - 56.4) < 1.5
              and got.get(2) and abs(got[2] - 112.8) < 4
              and got.get(4) and abs(got[4] - 225.6) < 10
              and turbo and turbo > 300)
        self.check('every speed runs at the rate it promises', ok, f'{got}, turbo {turbo}')

    def pauses(self):
        self.c.send({'c': 'pause', 'd': True})
        m = self.c.pump(5, until=lambda m: m.get('t') == 'emu' and m.get('paused'))
        before = self.c.frames
        self.c.pump(2)
        still = self.c.frames == before
        self.c.send({'c': 'pause', 'd': False})
        back = self.c.pump(5, until=lambda m: m.get('t') == 'emu' and not m.get('paused'))
        self.check('pause stops the game and lets it go again', bool(m) and still and bool(back))

    def points(self):
        ok = True
        for x, y in ((100, 50), (320, 200), (600, 380), (5, 5)):
            self.c.send({'c': 'aim', 'x': x, 'y': y})
            ok &= bool(self.c.pump(3, until=lambda m: m.get('t') == 'tele' and m.get('cursor') == [x, y]))
        self.c.send({'c': 'move', 'dx': -20, 'dy': -10})
        moved = self.c.pump(3, until=lambda m: m.get('t') == 'tele' and m.get('cursor') != [5, 5])
        self.check('the mouse goes exactly where it is pointed, and relative moves work',
                   ok and bool(moved), f'after a relative nudge: {moved and moved.get("cursor")}')

    def loads_a_save(self):
        self.title()
        m = self.load(LOAD1)
        self.check('a click on the title menu loads the game', bool(m) and m.get('in_play'),
                   f'scene {m and m.get("scene")}')
        return m

    def reads_the_game(self, tele):
        st = (tele or {}).get('stats') or {}
        eq = (tele or {}).get('equipment') or {}
        it = (tele or {}).get('items') or {}
        ok = (st.get('level', 0) > 0 and 0 < st.get('hp', 0) <= st.get('hp_max', 0)
              and len(eq) == 4 and len(it) == 4 and (tele or {}).get('names'))
        self.check('telemetry reads the hero out of memory', ok,
                   f'lvl {st.get("level")} hp {st.get("hp")}/{st.get("hp_max")} '
                   f'{list(eq.values())[:2]}')

    def reads_the_line(self):
        # ⚠️ Like the map, `text` is broadcast when it CHANGES; if the window was already read
        # before this check started listening, the last one is the one to look at.
        # ⚠️ Wait for a SETTLED window: the game types the message out, and the first reading is
        # usually half a line, which of course matches nothing in the script.
        m = self.c.pump(15, until=lambda m: m.get('t') == 'text'
                        and ((m.get('text') or {}).get('candidates')))
        t = ((m or {}).get('text') or self.c.last.get('text', {}).get('text')
             or (self.c.last.get('hello') or {}).get('text') or {})
        cand = (t.get('candidates') or [{}])[0]
        ok = self.check('the message window is read and located in the script',
                        bool(t.get('lines')) and cand.get('exact') and cand.get('editable'),
                        f'{cand.get("id")} {t.get("sources")} lines={t.get("lines")}')
        if not ok:
            shot = self.screen('message-window')
            print(f'      | the screen at that moment: {shot}', flush=True)
            self.why(r'message window|cannot read|cannot locate')
        return cand

    def refuses_a_bad_edit(self, cand):
        if not cand.get('id'):
            return self.check('a line the game cannot draw is refused', False, 'no candidate')
        before = (self.tmp / 'text' / f'{cand["file"]}.json').read_text()
        self.c.send({'c': 'text_edit', 'id': cand['id'], 'en': BAD_LINE})
        r = self.c.pump(8, until=lambda m: m.get('t') == 'text_edit')
        same = (self.tmp / 'text' / f'{cand["file"]}.json').read_text() == before
        self.check('a line the game cannot draw is refused and nothing is written',
                   bool(r) and not r.get('ok') and r.get('problems') and same,
                   (r or {}).get('problems'))

    def accepts_a_good_edit(self, cand):
        if not cand.get('id'):
            return self.check('a good edit lands in the proofreading copy', False, 'no candidate')
        new = '[Innkeeper]: Morning! Tidy the room before you leave.'
        self.c.send({'c': 'text_edit', 'id': cand['id'], 'en': new})
        r = self.c.pump(8, until=lambda m: m.get('t') == 'text_edit')
        rows = json.loads((self.tmp / 'text' / f'{cand["file"]}.json').read_text())
        row = next((x for x in rows if x['id'] == cand['id']), {})
        self.check('a good edit lands in the proofreading copy', bool(r and r.get('ok'))
                   and row.get('en') == new, f'{cand["id"]}')

    def keeps_snapshots(self):
        self.c.send({'c': 'slot_save', 'name': 'slot1'})
        m = self.c.pump(15, until=lambda m: m.get('t') == 'slots'
                        and any(s['name'] == 'slot1' and s['used'] for s in m['slots']))
        files = sorted(p.name for p in (self.tmp / 'states').glob('slot1.*'))
        pos_before = (self.c.last.get('tele', {}).get('pos') or {}).copy()
        self.c.send({'c': 'key', 'k': 'left', 'd': 1})
        time.sleep(0.15)
        self.c.send({'c': 'key', 'k': 'left', 'd': 0})
        self.c.pump(3)
        self.c.send({'c': 'slot_load', 'name': 'slot1'})
        ev = self.c.pump(15, until=lambda m: m.get('t') == 'event' and 'slot1' in m.get('msg', ''))
        self.c.pump(3)
        pos_after = self.c.last.get('tele', {}).get('pos') or {}
        self.check('a snapshot saves with a picture and loads back',
                   bool(m) and files == ['slot1.json', 'slot1.png', 'slot1.state']
                   and bool(ev) and ev.get('level') != 'error' and pos_after == pos_before,
                   f'{files}')

    def remembers_settings(self):
        self.c.send({'c': 'speed', 'v': 2})
        self.c.send({'c': 'sound', 'vol': 0.42})
        self.c.pump(3)
        saved = json.loads((self.tmp / 'settings.json').read_text())
        self.c.send({'c': 'speed', 'v': 1})
        self.c.pump(2)
        self.check('settings are remembered on disk', saved.get('speed') == 2
                   and abs(saved.get('volume', 0) - 0.42) < 0.01, f'{saved}')

    def fights_on_its_own(self, budget=150):
        """Autobattle: it walks, it fights, and the human takes the game back at a touch."""
        self.title()
        m = self.load(LOAD2)
        if not m:
            return self.check('autobattle walks and fights', False, 'could not load the dungeon save')
        self.c.pump(3)
        start = (self.c.last.get('tele', {}).get('pos') or {}).copy()
        self.c.send({'c': 'grind', 'on': True, 'hp_floor': 20})
        end, fights, cells, scenes = time.time() + budget, 0, set(), set()
        stopped = None
        while time.time() < end:
            got = self.c.pump(2)
            tele = self.c.last.get('tele', {})
            emu = self.c.last.get('emu', {})
            p = tele.get('pos') or {}
            if p.get('x') is not None:
                cells.add((p['x'], p['y']))
            if tele.get('scene'):
                scenes.add(tele['scene'])
            fights = max(fights, ((emu.get('grind') or {}).get('fights') or 0))
            if emu.get('grind') and not emu['grind']['on'] and emu['grind'].get('stopped'):
                stopped = emu['grind']['stopped']
                break
            if fights and len(cells) > 2:
                break
        # ⚠️ What "working" means here. The saves this runs from start in a dead-end spur whose
        # only other way out is the door of the hero's room -- and behind that door the game
        # opens its diary (save) menu, which holds the input. Demanding a FIGHT from there asked
        # for the impossible. Autobattle is doing its job if it walks and then either fights or
        # hands the game back with a reason; what must never happen is silence or poking a menu.
        moved = len(cells) > 1
        ok = self.check('autobattle walks the floor, and fights or hands the game back',
                        moved and (fights > 0 or stopped),
                        f'{len(cells)} cells, {fights} fights, scenes {sorted(scenes)[:3]}'
                        + (f', stopped: {stopped}' if stopped else ''))
        if not ok:
            self.screen('autobattle')
            self.why(r'grind |tap|keys \[', n=16)
        if not stopped:
            self.c.send({'c': 'key', 'k': 'space', 'd': 1})
            self.c.send({'c': 'key', 'k': 'space', 'd': 0})
            ev = self.c.pump(8, until=lambda m: m.get('t') == 'event' and 'controls' in m.get('msg', ''))
            self.check('touching the controls stops autobattle at once', bool(ev),
                       (ev or {}).get('msg'))
        else:
            self.check('touching the controls stops autobattle at once', True,
                       'it had already stopped on its own: ' + stopped)

    def draws_the_map(self):
        # ⚠️ The map is broadcast when it CHANGES; standing still, the last one is the one from
        # `hello`. Waiting for a fresh message here reported "0 cells" for a perfectly good map.
        m = (self.c.pump(10, until=lambda m: m.get('t') == 'map' and m.get('cells'))
             or self.c.last.get('map')
             or (self.c.last.get('hello') or {}).get('map') or {})
        cells = (m or {}).get('cells') or {}
        here = (m or {}).get('here') or []
        pos = self.c.last.get('tele', {}).get('pos') or {}
        self.check('the floor map comes from the game and shows where you stand',
                   len(cells) > 20 and here[:2] == [pos.get('x'), pos.get('y')],
                   f'{len(cells)} cells, you at {here}')

    def survives_a_crash(self):
        pid = self.c.last.get('emu', {}).get('pid')
        os.kill(pid, signal.SIGKILL)
        crashed = self.c.pump(15, until=lambda m: m.get('t') == 'emu' and m.get('state') == 'crashed')
        back = self.c.pump(60, until=lambda m: m.get('t') == 'emu' and m.get('state') == 'running'
                           and m.get('pid') != pid and m.get('mem'))
        self.check('a killed emulator is reported and restarted', bool(crashed) and bool(back),
                   f'new pid {back and back.get("pid")}')

    def survives_a_hang(self):
        pid = self.c.last.get('emu', {}).get('pid')
        os.kill(pid, signal.SIGSTOP)
        back = self.c.pump(90, until=lambda m: m.get('t') == 'emu' and m.get('state') == 'running'
                           and m.get('pid') != pid and m.get('mem'))
        if not back:
            os.kill(pid, signal.SIGCONT)
        self.check('an emulator that stops answering is killed and restarted', bool(back),
                   f'new pid {back and back.get("pid")}')

    def serves_two_tabs(self):
        second = Client(self.port)
        hello = second.pump(10, until=lambda m: m.get('t') == 'hello')
        second.pump(3)
        first_before = self.c.frames
        self.c.pump(2)
        both = second.frames > 0 and self.c.frames >= first_before
        second.s.close()
        self.c.pump(2)
        alive = bool(self.c.pump(5, until=lambda m: m.get('t') == 'emu'))
        self.check('two tabs work, and closing one leaves the other alone',
                   bool(hello) and both and alive, f'second tab got {second.frames} frames')

    def leaves_nothing_behind(self):
        pid = (self.c.last.get('emu', {}) if self.c else {}).get('pid')
        self.stop()
        time.sleep(0.5)
        gone = pid is None or not pathlib.Path(f'/proc/{pid}').exists()
        err = self.proc.stderr.read().decode(errors='replace')
        self.check('stopping the cockpit leaves no emulator behind', gone)
        after = self.footprint()
        touched = sorted(p for p in set(self.before) | set(after) if self.before.get(p) != after.get(p))
        self.check('the tree under test is left exactly as it was', not touched, touched[:6])
        if err.strip():
            print('--- cockpit stderr ---\n' + err[-1500:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tree', default=str(HERE), help='the emu/ directory to run')
    ap.add_argument('--disk', default=str(HERE.parent / 'game/WordsWorth_play.hdi'))
    ap.add_argument('--skip', default='', help='"slow" leaves out the 30 s hang check')
    ap.add_argument('--keep', action='store_true', help='keep the temporary tree')
    ap.add_argument('--system', default=None,
                    help='directory with the np2kai BIOS (default: <tree>/system)')
    a = ap.parse_args()
    print(f'=== cockpit acceptance: {a.tree}\n    disk {pathlib.Path(a.disk).name}', flush=True)
    r = Run(a.tree, a.disk, keep=a.keep, system=a.system)
    try:
        if not r.answers():
            r.stop()
            return 1
        r.serves_the_page()
        if not r.boots():
            r.stop()
            return 1
        r.talks()
        r.paces()
        r.pauses()
        r.points()
        tele = r.loads_a_save()
        r.reads_the_game(tele)
        cand = r.reads_the_line()
        r.refuses_a_bad_edit(cand)
        r.accepts_a_good_edit(cand)
        r.keeps_snapshots()
        r.remembers_settings()
        r.serves_two_tabs()
        r.fights_on_its_own()
        r.draws_the_map()
        r.survives_a_crash()
        if 'slow' not in a.skip:
            r.survives_a_hang()
    except Exception as e:
        import traceback
        traceback.print_exc()
        r.check('no exception during the run', False, repr(e))
    finally:
        try:
            r.leaves_nothing_behind()
        except Exception as e:
            r.check('clean shutdown', False, repr(e))
    good = sum(1 for _, ok in r.results if ok)
    bad = [n for n, ok in r.results if not ok]
    print(f'\n{"✅" if not bad else "❌"} cockpit acceptance: {good}/{len(r.results)}'
          + (f'\n   failed: {", ".join(bad)}' if bad else ''))
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
