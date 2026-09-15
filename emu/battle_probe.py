"""Walk into a battle without a model and report whether the image survived it.

Tweaking the numbers killed the game right at the moment SENTO00.MES loads (battle start), while the small probe
(ab_probe: single `up` press) never reaches a battle and so declares everything alive.
Here we walk the corridor until the scene becomes a battle, and only then deliver the verdict.

    emu/.venv/bin/python emu/battle_probe.py <image in scratch/emuprobe>

⚠️ ONE at a time. /tmp here is tmpfs, so the image (20 MB) and frames sit in RAM
alongside the llama.cpp model (~35 GB), and two probes in parallel have already killed background tasks due
to memory exhaustion. Delete images after checking.
"""
import os, pathlib, sys
import numpy as np
from libretro import Session
from libretro.drivers import ExplicitPathDriver, IterableInputDriver
from libretro.api.input.keyboard import KeyboardState
from libretro.api.input.joypad import JoypadState

# ⚠️ The root is resolved relative to THIS file, not hardcoded as an absolute path: the probe is launched and
# from a repository copy, and from someone else's clone.
EMU = pathlib.Path(__file__).resolve().parent; WW = EMU.parent
# ⚠️ The probe directory is NOT tied to the agent session. Previously, a path to one specific
# scratchpad, and when launched from another session the probe would silently find nothing.
SCR = pathlib.Path(os.environ.get('WW_SCRATCH') or '/tmp/ww-probe')
SCR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(EMU)); import state as S, boot                # noqa: E402
CORE = "/usr/lib/libretro/np2kai_libretro.so"
# ⚠️ The kernel WRITES to the system directory (np2kai.cfg). A probe next to a running agent shares with
# this file -- suspect in the death of agent 2026-09-10. WW_SYSTEM gives the probe its own copy.
SYSTEM = pathlib.Path(os.environ.get("WW_SYSTEM") or (EMU / "system"))
img = sys.argv[1]
tag = pathlib.Path(img).stem   # ⚠️ frame name — from stem, not from the path: a full path embedded in the filename drops the probe
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 90

tap = {'k': None, 'n': 0}
def gen():
    while True:
        if tap['n'] > 0:
            tap['n'] -= 1
            yield KeyboardState(**{tap['k']: True}) if tap['k'] else JoypadState()
        else:
            yield JoypadState()

sess = Session(core=CORE, game=SCR / 'emuprobe' / img,
               path=ExplicitPathDriver(corepath=CORE, system=SYSTEM,
                                       save=SCR / 'emuprobe/save', assets=SYSTEM),
               input=IterableInputDriver(gen()), vfs=None)

def frame():
    av = sess.video._current; d = av._frame_dims
    if d is None or not av._frame:
        return None
    raw = np.frombuffer(av._frame, dtype=np.uint16).reshape(d.height, d.width)
    return np.dstack([((raw >> 11) & 0x1F) << 3, ((raw >> 5) & 0x3F) << 2,
                      (raw & 0x1F) << 3]).astype(np.uint8)

def run(n):
    for _ in range(n):
        sess.run()

def press(k, hold=8, then=140):
    tap['k'] = k; tap['n'] = hold; run(hold + then)

def snap():
    b = bytearray(sess.core.serialize_size()); sess.core.serialize(b); return bytes(b)

def health():
    s = [snap()]
    for _ in range(3):
        run(90); s.append(snap())
    return S.churn(s)

# ⚠️ Encounters are random (the counter is seeded by the timer), so "no encounters in 60 battle steps" — this
# NOT "image is alive", it's "unverified". We proceed until a conflict occurs, and only then do we judge.
# pressing a direction first TURNS, and a route of only turns spins
# in place: mostly go forward, turning off every sixth step
WALK = ['up'] * 6 + ['right'] + ['up'] * 6 + ['left']
with sess:
    run(600)
    ok, _ = boot.drive(press, run, frame, log=lambda m: None)
    print(f'{img}: boot ok={ok} scene={S.identify(snap())["scene"]}', flush=True)
    seen_fight = False
    for i in range(LIMIT):
        press(WALK[i % len(WALK)])
        sc = str(S.identify(snap())['scene'])
        h = health()
        if h['class'] not in ('idle', 'busy'):
            print(f'{img}: DIED at step {i} ({h["class"]}), scene {sc}, battle seen={seen_fight}')
            f = frame()
            if f is not None:
                from PIL import Image
                Image.fromarray(f).save(SCR / f'died_{tag}.png')
            break
        if 'SENTO' in sc:
            if not seen_fight:
                print(f'{img}: battle at step {i}, scene {sc}', flush=True)
            seen_fight = True
            from PIL import Image
            for k in range(30):                     # we hold our ground until the end of the battle and capture every frame
                press('space')
                f = frame()
                if f is not None:
                    Image.fromarray(f).save(SCR / f'fight_{tag}_{k:02d}.png')
            h = health()
            if h['class'] not in ('idle', 'busy'):
                print(f'{img}: DIED IN BATTLE ({h["class"]}), scene {S.identify(snap())["scene"]}')
                from PIL import Image
                f = frame()
                if f is not None:
                    Image.fromarray(f).save(SCR / f'died_{tag}.png')
                break
            print(f'{img}: BATTLE SURVIVED at step {i}, stats={S.stats(snap())}')
            break
    else:
        print(f'{img}: {LIMIT} steps without a battle -- NOT VERIFIED, stats={S.stats(snap())}')
    from PIL import Image
    f = frame()
    if f is not None:
        Image.fromarray(f).save(SCR / f'end_{tag}.png')
