"""Walk to a target cell on the floor using the in-memory map — and report what happened along the way.

Why. To reproduce the crash in §27, we need to reach the room with event 3 on `FLOOR02`.
Wandering blindly is pointless: 150 steps of "forward-turn" found nothing. But the floor map sits
in memory (`state.floormap`), each cell has its sides and event number recorded — so the path
is not searched for, it is COMPUTED, and walking reduces to "turn and step".

What it handles along the way:
  * knows where it stands from memory (`state.where`), not from the screen;
  * catches random encounters (scene becomes `SENTO*`) and dismisses them with space;
  * after each step checks whether the game is alive — a stack of just `START.MES` means death;
  * grabs a frame and reads the message window, so there is evidence.

    emu/.venv/bin/python emu/goto.py <image.hdi> <x> <y>
    emu/.venv/bin/python emu/goto.py <image.hdi> --event 3
"""
import collections
import os
import pathlib
import shutil
import sys

import numpy as np
from libretro import Session
from libretro.drivers import ExplicitPathDriver, IterableInputDriver
from libretro.api.input.keyboard import KeyboardState
from libretro.api.input.joypad import JoypadState
from PIL import Image

EMU = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(EMU))
import boot                                                          # noqa: E402
import state as S                                                    # noqa: E402
import textbox                                                       # noqa: E402

CORE = '/usr/lib/libretro/np2kai_libretro.so'
SCR = pathlib.Path(os.environ.get('WW_SCRATCH') or '/tmp/ww-goto')
SYSTEM = pathlib.Path(os.environ.get('WW_SYSTEM') or (EMU / 'system'))
SCR.mkdir(parents=True, exist_ok=True)
OUT = SCR / 'frames'
OUT.mkdir(exist_ok=True)

src = pathlib.Path(sys.argv[1]).resolve()
want_event = None
target = None
if sys.argv[2] == '--event':
    want_event = int(sys.argv[3])
else:
    target = (int(sys.argv[2]), int(sys.argv[3]))

img = SCR / src.name
if not img.exists() or img.stat().st_mtime < src.stat().st_mtime:
    shutil.copyfile(src, img)

tap = {'k': None, 'n': 0}


def gen():
    while True:
        if tap['n'] > 0:
            tap['n'] -= 1
            yield KeyboardState(**{tap['k']: True}) if tap['k'] else JoypadState()
        else:
            yield JoypadState()


sess = Session(core=CORE, game=img,
               path=ExplicitPathDriver(corepath=CORE, system=SYSTEM,
                                       save=SCR / 'save', assets=SYSTEM),
               input=IterableInputDriver(gen()), vfs=None)


def frame():
    av = sess.video._current
    d = av._frame_dims
    if d is None or not av._frame:
        return None
    raw = np.frombuffer(av._frame, dtype=np.uint16).reshape(d.height, d.width)
    return np.dstack([((raw >> 11) & 0x1F) << 3, ((raw >> 5) & 0x3F) << 2,
                      (raw & 0x1F) << 3]).astype(np.uint8)


def run(n):
    for _ in range(n):
        sess.run()


def press(k, hold=8, then=140):
    tap['k'] = k
    tap['n'] = hold
    run(hold + then)


def snap():
    b = bytearray(sess.core.serialize_size())
    sess.core.serialize(b)
    return bytes(b)


def look(tag):
    f = frame()
    txt = ''
    if f is not None:
        im = Image.fromarray(f)
        im.save(OUT / f'{tag}.png')
        try:
            txt = ' / '.join(l.strip() for l in textbox.lines(im.convert('RGB')) if l.strip())
        except Exception:
            pass
    return txt


def route(fm, start, goal):
    """Shortest path on a grid: breadth-first search, passages taken from the game itself."""
    prev = {start: None}
    q = collections.deque([start])
    while q:
        cur = q.popleft()
        if cur == goal:
            break
        x, y = cur
        for side in (0, 1, 2, 3):
            if S.map_side(fm, x, y, side) != 'open':
                continue
            dx, dy = S.STEP[side]
            nxt = (x + dx, y + dy)
            if nxt in fm['cells'] and nxt not in prev:
                prev[nxt] = (cur, side)
                q.append(nxt)
    if goal not in prev:
        return None
    path, cur = [], goal
    while prev[cur] is not None:
        cur, side = prev[cur]
        path.append(side)
    return list(reversed(path))


with sess:
    run(600)
    ok, _ = boot.drive(press, run, frame, log=lambda m: None)
    st = S.identify(snap())
    print(f'boot: scene={st["scene"]} stack={st.get("stack")}', flush=True)
    press('up')                       # ⚠️ one step: right after loading the map is still not the right one
    sn = snap()
    fm = S.floormap(sn)
    if not fm:
        sys.exit('map not found')
    here = S.where(sn)
    start = (here['x'], here['y'])
    if want_event is not None:
        hits = [xy for xy, c in fm['cells'].items() if c['event'] == want_event]
        if not hits:
            sys.exit(f'event {want_event} not found on this floor')
        target = hits[0]
    print(f'standing at {start}, heading {here["facing"]}, walking to {target}', flush=True)

    path = route(fm, start, target)
    if path is None:
        sys.exit(f'no path from {start} to {target} on the map')
    print(f'path: {len(path)} steps, sides {path}', flush=True)

    facing = here['facing']
    for i, side in enumerate(path):
        # ⚠️ `left` rotates heading by +1 wrapping around — this is verified, but which side is "north" — no.
        while facing != side:
            press('left')
            facing = (facing + 1) % 4
        press('up')
        sn = snap()
        st = S.identify(sn)
        scene, stack = str(st['scene']), (st.get('stack') or [])
        txt = look(f'{i + 1:03d}')
        w = S.where(sn)
        print(f'{i + 1:3d}/{len(path)} -> {(w["x"], w["y"])} heading {w["facing"]}  '
              f'{scene:16} {txt[:60]}', flush=True)
        if len(stack) < 2:
            print(f'❌ GAME DIED at step {i + 1}: stack={stack}', flush=True)
            break
        if 'SENTO' in scene:                       # random encounter — fending off
            print('in a meeting, will reply', flush=True)
            for _ in range(40):
                press('space')
                if 'SENTO' not in str(S.identify(snap())['scene']):
                    break
            facing = S.where(snap())['facing']     # after the battle the course could have changed
    else:
        print('arrived', flush=True)

    # ⚠️ Just arriving isn't enough: the event unfolds via dialog lines and menus. We push through and watch health —
    # crash §27 happened AFTER the replica, not on entry into the cell.
    # `WW_REPEAT` — how many TIMES to enter the event: a player reported it killed them on the SECOND entry,
    # and a single check, by definition, misses this.
    # ⚠️ `WW_SEQ` — exact sequence instead of blindly forcing `return`. Crash
    # 2026-09-14 occurs after `up` pressed AT THE END of the scene, finalized by bare `return`
    # it's not caught: player pressed return 62 times, then up, then five more return.
    seq = os.environ.get('WW_SEQ')
    if seq:
        keys = []
        for part in seq.split(','):
            k, _, n = part.partition('*')
            keys += [k] * int(n or 1)
        print(f'\n-- exact sequence: {len(keys)} presses --', flush=True)
        for i, k in enumerate(keys, 1):
            press(k)
            f = frame()
            black = float((f.sum(axis=2) > 24).mean()) if f is not None else 1.0
            txt = look(f'seq_{i:03d}_{k}')
            st = S.identify(snap())
            mark = ''
            if black < 0.01:
                run(600)
                f2 = frame()
                if f2 is not None and float((f2.sum(axis=2) > 24).mean()) < 0.01:
                    mark = '❌ BLACK SCREEN — EXITED TO DOS'
            print(f'  {i:3d} {k:12} screen {100 * black:5.1f}%  '
                  f'memory={str(st["scene"]):14} {txt[:44]}{mark}', flush=True)
            if mark:
                print(f'\n❌ REPRODUCED on press {i} ({k}); memory says: '
                      f'{S.identify(snap())["stack"]}', flush=True)
                break
        else:
            print('\n✅ sequence passed, game alive', flush=True)
        print(f'frames: {OUT}', flush=True)
        raise SystemExit(0)

    dead = False
    for rep in range(int(os.environ.get('WW_REPEAT', '1'))):
        print(f'\n-- attempt {rep + 1} --', flush=True)
        for j in range(int(os.environ.get('WW_AFTER', '16'))):
            press('return_key')
            sn = snap()
            st = S.identify(sn)
            stack = st.get('stack') or []
            txt = look(f'r{rep + 1}_after_{j + 1:02d}')
            print(f'  {j + 1:2d} {str(st["scene"]):16} stack={len(stack)}  {txt[:70]}', flush=True)
            if len(stack) < 2:
                print(f'❌ GAME DIED on pass {rep + 1}, extra press {j + 1}: stack={stack}',
                      flush=True)
                dead = True
                break
        if dead:
            break
        # step off the cell and return: the event will fire again
        back = (path[-1] + 2) % 4 if path else 2
        for side in (back, (back + 2) % 4):
            while facing != side:
                press('left')
                facing = (facing + 1) % 4
            press('up')
            w = S.where(snap())
            print(f'   step -> {(w["x"], w["y"])}', flush=True)
    print(f'frames: {OUT}', flush=True)
