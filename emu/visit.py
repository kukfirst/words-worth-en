#!/usr/bin/env python3
"""Загрузиться в заданную сцену и пройти её нажатиями — вердикт по ЭКРАНУ.

Зачем отдельно от `goto.py`. Тот ходит по карте этажа, и в лавке, на постоялом дворе или в
меню карты нет вовсе: `state.floormap` возвращает пусто, и goto выходит, не начав. А
проверять лавки надо: их реплики о деньгах собраны из половин (`tools/shopfix.py`) и
ломались именно они.

⚠️ Жив или мёртв решает ЭКРАН, а не память. Выходя в DOS, игра память не чистит, и
`state.identify` ещё долго рапортует сцену резидентной — так пропустили вылет §30.

⚠️ Телепорт делает вызывающий: имя сцены лежит ASCII-строкой в начале слота `FLAG0`.

    WW_SEQ="return_key*6,down,return_key*4" emu/.venv/bin/python emu/visit.py <образ.hdi>
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
sys.path.insert(0, str(EMU))
import boot                                                          # noqa: E402
import state as S                                                    # noqa: E402
import textbox                                                       # noqa: E402

CORE = '/usr/lib/libretro/np2kai_libretro.so'
SCR = pathlib.Path(os.environ.get('WW_SCRATCH') or '/tmp/ww-visit')
SYSTEM = pathlib.Path(os.environ.get('WW_SYSTEM') or (EMU / 'system'))
SCR.mkdir(parents=True, exist_ok=True)
OUT = SCR / 'frames'
OUT.mkdir(exist_ok=True)
BLACK = 0.01                       # доля незачернённого экрана, ниже которой это DOS

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


def keys(seq):
    out = []
    for part in seq.split(','):
        k, _, n = part.partition('*')
        out += [k] * int(n or 1)
    return out


with sess:
    run(600)
    boot.drive(press, run, frame, log=lambda m: None)
    snap = bytearray(sess.core.serialize_size())
    sess.core.serialize(snap)
    print(f'загрузка: сцена={S.identify(bytes(snap))["scene"]}', flush=True)
    seen, dead = set(), False
    for i, k in enumerate(keys(os.environ.get('WW_SEQ', 'return_key*20')), 1):
        press(k)
        f = frame()
        lit = float((f.sum(axis=2) > 24).mean()) if f is not None else 0.0
        txt = ''
        if f is not None:
            im = Image.fromarray(f)
            im.save(OUT / f'{i:03d}_{k}.png')
            try:
                txt = ' / '.join(l.strip() for l in textbox.lines(im.convert('RGB')) if l.strip())
            except Exception:
                pass
        if txt:
            seen.add(txt)
        mark = ''
        if lit < BLACK:
            run(600)
            f2 = frame()
            if f2 is not None and float((f2.sum(axis=2) > 24).mean()) < BLACK:
                mark = '  ❌ ЭКРАН ЧЁРНЫЙ — ВЫШЛА В DOS'
                dead = True
        print(f'  {i:3d} {k:12} экран {100 * lit:5.1f}%  {txt[:56]}{mark}', flush=True)
        if dead:
            break
    print(f'\nразных реплик показано: {len(seen)}')
    print('❌ ВЫШЛА В DOS' if dead else '✅ сцена пройдена, игра жива', flush=True)
    print(f'кадры: {OUT}', flush=True)
raise SystemExit(1 if dead else 0)
