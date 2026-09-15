"""Where on the floor the event with the wanted number sits -- from the in-memory map, not by foot.

The branch where the game crashed (§27) is gated by `V == 3`, and `V` comes from the
`(field 261)` opcode: the engine walks the player and returns the number of the event they
stepped on. The event number is recorded RIGHT IN THE MAP -- `state.floormap()` parses cells
and hands back an `event` field on each. So the target room doesn't need a walk to find; it
can just be read.

    emu/.venv/bin/python emu/findevent.py <image.hdi>
"""
import os
import pathlib
import shutil
import sys

import numpy as np
from libretro import Session
from libretro.drivers import ExplicitPathDriver, IterableInputDriver
from libretro.api.input.keyboard import KeyboardState
from libretro.api.input.joypad import JoypadState

EMU = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(EMU))
import boot                                                          # noqa: E402
import state as S                                                    # noqa: E402

CORE = '/usr/lib/libretro/np2kai_libretro.so'
SCR = pathlib.Path(os.environ.get('WW_SCRATCH') or '/tmp/ww-findevent')
SYSTEM = pathlib.Path(os.environ.get('WW_SYSTEM') or (EMU / 'system'))
SCR.mkdir(parents=True, exist_ok=True)

src = pathlib.Path(sys.argv[1]).resolve()
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


with sess:
    run(600)
    ok, _ = boot.drive(press, run, frame, log=lambda m: None)
    st = S.identify(snap())
    print(f'scene={st["scene"]}  stack={st.get("stack")}')
    # ⚠️ One step AFTER loading: right after it, position and map still aren't the game's real ones.
    press('up')
    sn = snap()
    print(f'position={S.where(sn)}')
    fm = S.floormap(sn)
    if not fm:
        sys.exit('map not found in memory')
    print(f'map: {fm["w"]}x{fm["h"]} at address 0x{fm["addr"]:x}, signature {fm["sig"]}')
    ev = {}
    for (x, y), c in fm['cells'].items():
        if c['event']:
            ev.setdefault(c['event'], []).append((x, y, c['flag']))
    print(f'\ncells with an event: {sum(len(v) for v in ev.values())}')
    for k in sorted(ev):
        cells = ', '.join(f'({x},{y}) flag={f}' for x, y, f in sorted(ev[k]))
        mark = 'THIS' if k == 3 else ''
        print(f'  event {k:3d}: {cells}{mark}')
