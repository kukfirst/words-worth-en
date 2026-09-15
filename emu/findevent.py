"""Где на этаже лежит событие с нужным номером — по карте из памяти, а не ногами.

Ветка, на которой игра вылетела (§27), закрыта условием `V == 3`, а `V` приходит из опкода
`(field 261)`: движок водит игрока и возвращает номер события, на которое тот наступил.
Номер события записан В САМОЙ КАРТЕ — `state.floormap()` разбирает клетки и отдаёт у каждой
поле `event`. Значит нужную комнату можно не искать обходом, а прочитать.

    emu/.venv/bin/python emu/findevent.py <образ.hdi>
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
    print(f'сцена={st["scene"]}  стек={st.get("stack")}')
    # ⚠️ Один шаг ПОСЛЕ загрузки: сразу после неё позиция и карта ещё не те, что в игре.
    press('up')
    sn = snap()
    print(f'позиция={S.where(sn)}')
    fm = S.floormap(sn)
    if not fm:
        sys.exit('карта не найдена в памяти')
    print(f'карта: {fm["w"]}x{fm["h"]} по адресу 0x{fm["addr"]:x}, подпись {fm["sig"]}')
    ev = {}
    for (x, y), c in fm['cells'].items():
        if c['event']:
            ev.setdefault(c['event'], []).append((x, y, c['flag']))
    print(f'\nклеток с событием: {sum(len(v) for v in ev.values())}')
    for k in sorted(ev):
        cells = ', '.join(f'({x},{y}) flag={f}' for x, y, f in sorted(ev[k]))
        mark = '   <<< ЭТА' if k == 3 else ''
        print(f'  событие {k:3d}: {cells}{mark}')
