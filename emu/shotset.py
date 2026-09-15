"""Grab a set of frames off a FINISHED image: boot, title menu, shop, battle.

Why separate from the probe. `battle_probe.py` judges whether an image is alive and grabs
whatever falls out along the way. The showcase needs DIFFERENT screens with English text,
and captured from exactly the image that ships to people -- otherwise the picture ends up
showing text from a previous build.

What it does: cold start following the `boot.RECIPE` recipe with a frame after each step,
then the `FLOOR05 -> SHP_5I` route (the shop -- home to the fixed purchase question), then a
walk to a random encounter with a frame on every keypress.

⚠️ ONE AT A TIME: the core writes into the system directory, while the image and frames sit
in tmpfs next to the model. `WW_SYSTEM` provides its own copy of the system directory so a
running agent isn't disturbed.

    emu/.venv/bin/python emu/shotset.py <image.hdi> <destination>
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
from PIL import Image

EMU = pathlib.Path(__file__).resolve().parent
WW = EMU.parent
sys.path.insert(0, str(EMU))
import boot                                                          # noqa: E402
import routes                                                        # noqa: E402
import state as S                                                    # noqa: E402
import textbox                                                       # noqa: E402

CORE = '/usr/lib/libretro/np2kai_libretro.so'
SCR = pathlib.Path(os.environ.get('WW_SCRATCH') or '/tmp/ww-shots')
SYSTEM = pathlib.Path(os.environ.get('WW_SYSTEM') or (EMU / 'system'))

src = pathlib.Path(sys.argv[1]).resolve()
out = pathlib.Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
SCR.mkdir(parents=True, exist_ok=True)
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


shots = []


def snap_png(name):
    """Frame to disk + the window text read off it -- so shots can be picked by CONTENT."""
    f = frame()
    if f is None:
        return
    p = out / f'{len(shots):03d}_{name}.png'
    im = Image.fromarray(f)
    im.save(p)
    try:
        lines = [l.rstrip() for l in textbox.lines(im.convert('RGB'))]
    except Exception:
        lines = []
    txt = '\n'.join(l for l in lines if l.strip())
    shots.append((p.name, txt))
    print(f'  {p.name:34s} {txt[:70]!r}', flush=True)


def scene():
    b = bytearray(sess.core.serialize_size())
    sess.core.serialize(b)
    return str(S.identify(bytes(b))['scene'])


with sess:
    print('== cold start')
    run(600)
    # boot.RECIPE, but with a frame after every step
    for label, key, then, _must in boot.RECIPE:
        if key is None:
            run(then)
        else:
            press(key, 8, then)
        snap_png(label.replace(': ', '_').replace(' ', '-').replace('/', '-'))
    print(f'  scene after boot: {scene()}')

    # ⚠️ A new game starts in the HERO'S ROOM (CAMP.MES), while the route to the shop is
    # recorded from the corridor (FLOOR05.MES). Without the first leg the second never
    # starts, and the probe spends forty steps pacing the room, reporting "no meeting took
    # place".
    print('== to the corridor and the shop')
    # ⚠️ A route that doesn't match the CURRENT scene isn't a chain failure -- it's just not
    # its turn yet: after boot the game lands either in the hero's room or already in the
    # corridor. Previously the first "wrong place" would abort the path, and the shop was
    # never captured once.
    all_routes = {x['name']: x for x in routes.load_all()}
    ok = False
    # ⚠️ The `FLOOR05 -> SHP_5I` route does NOT walk from here: the scene matches by name,
    # but the starting point differs -- a new game starts in the hero's room, and the route
    # is recorded starting from the corridor. Checking location by scene name doesn't catch
    # that, so fourteen presses walked the hero into a corner, and the walk that followed
    # found no encounter at all (two runs in a row). Not calling it.
    for name in ('CAMP.MES--to--FLOOR05.MES',):
        r = all_routes.get(name)
        if r is None:
            print(f'  no route {name}')
            continue
        ok, why = routes.replay(r, press, run, scene)
        print(f'  {name}: {ok} -- {why}')
        snap_png('route-' + name.split('--to--')[1].split('.')[0])
    if ok:
        # counter: confirm the greeting, enter "Buy", walk the item list. Keys were not
        # guessed: `space` confirms, `down` moves through the list. A frame is captured on
        # EVERY press, and the ones we want get picked afterward by the text they show.
        for i, k in enumerate(['space'] * 3 + ['space', 'down', 'space',
                                              'space', 'down', 'down', 'space'] * 2):
            press(k)
            snap_png(f'shop-{i:02d}-{k}')

    print('See you later')
    walk = ['up'] * 6 + ['right'] + ['up'] * 6 + ['left']
    for i in range(90):
        press(walk[i % len(walk)])
        sc = scene()
        if 'SENTO' in sc:
            print(f'  battle at step {i}: {sc}')
            for k in range(14):
                press('space')
                snap_png(f'fight-{k:02d}')
            break
    else:
        print('no meeting took place')
        snap_png('walk-end')

    # items menu: Escape in this game opens exactly that (see STATUS §24b)
    print('== items menu')
    press('escape')
    snap_png('items-menu')
    press('down')
    snap_png('items-menu-2')

print(f'\nframes captured: {len(shots)} -> {out}')
