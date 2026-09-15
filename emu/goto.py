"""Дойти до заданной клетки этажа по карте из памяти — и рассказать, что случилось по пути.

Зачем. Чтобы воспроизвести вылет §27, надо попасть в комнату с событием 3 на `FLOOR02`.
Бродить наугад бесполезно: 150 шагов «вперёд-поворот» не нашли ничего. Но карта этажа лежит
в памяти (`state.floormap`), у каждой клетки записаны стороны и номер события — значит путь
не ищется, а СЧИТАЕТСЯ, а ходьба сводится к «повернуться и шагнуть».

Что умеет по дороге:
  * знает, где стоит, по памяти (`state.where`), а не по картинке;
  * ловит случайные встречи (сцена становится `SENTO*`) и отбивается пробелом;
  * после каждого шага проверяет, жива ли игра — стек из одного `START.MES` это смерть;
  * снимает кадр и читает окно сообщения, чтобы было чем доказывать.

    emu/.venv/bin/python emu/goto.py <образ.hdi> <x> <y>
    emu/.venv/bin/python emu/goto.py <образ.hdi> --event 3
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
    """Кратчайший путь по клеткам: ширина в ширину, проходы берём у самой игры."""
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
    print(f'загрузка: сцена={st["scene"]} стек={st.get("stack")}', flush=True)
    press('up')                       # ⚠️ один шаг: сразу после загрузки карта ещё не та
    sn = snap()
    fm = S.floormap(sn)
    if not fm:
        sys.exit('карта не найдена')
    here = S.where(sn)
    start = (here['x'], here['y'])
    if want_event is not None:
        hits = [xy for xy, c in fm['cells'].items() if c['event'] == want_event]
        if not hits:
            sys.exit(f'события {want_event} на этаже нет')
        target = hits[0]
    print(f'стоим в {start}, курс {here["facing"]}, идём в {target}', flush=True)

    path = route(fm, start, target)
    if path is None:
        sys.exit(f'пути из {start} в {target} по карте нет')
    print(f'путь: {len(path)} шагов, стороны {path}', flush=True)

    facing = here['facing']
    for i, side in enumerate(path):
        # ⚠️ `left` крутит курс на +1 по кругу — это проверено, а какая сторона «север», нет.
        while facing != side:
            press('left')
            facing = (facing + 1) % 4
        press('up')
        sn = snap()
        st = S.identify(sn)
        scene, stack = str(st['scene']), (st.get('stack') or [])
        txt = look(f'{i + 1:03d}')
        w = S.where(sn)
        print(f'{i + 1:3d}/{len(path)} -> {(w["x"], w["y"])} курс {w["facing"]}  '
              f'{scene:16} {txt[:60]}', flush=True)
        if len(stack) < 2:
            print(f'❌ ИГРА УМЕРЛА на шаге {i + 1}: стек={stack}', flush=True)
            break
        if 'SENTO' in scene:                       # случайная встреча — отбиваемся
            print('   встреча, отбиваюсь', flush=True)
            for _ in range(40):
                press('space')
                if 'SENTO' not in str(S.identify(snap())['scene']):
                    break
            facing = S.where(snap())['facing']     # после боя курс мог смениться
    else:
        print('дошли', flush=True)

    # ⚠️ Прийти мало: событие разворачивается репликами и меню. Дожимаем и следим за жизнью —
    # вылет §27 случился ПОСЛЕ реплики, а не на входе в клетку.
    # `WW_REPEAT` — сколько РАЗ зайти в событие: игрок сообщил, что убило со ВТОРОГО захода,
    # и одиночная проверка такое по определению пропускает.
    # ⚠️ `WW_SEQ` — точная последовательность вместо слепого дожима `return`. Вылет
    # 2026-09-14 случается после `up`, нажатого В КОНЦЕ сцены, и добивкой одними `return`
    # он не ловится: игрок жал 62 раза return, потом up, потом ещё пять return.
    seq = os.environ.get('WW_SEQ')
    if seq:
        keys = []
        for part in seq.split(','):
            k, _, n = part.partition('*')
            keys += [k] * int(n or 1)
        print(f'\n-- точная последовательность: {len(keys)} нажатий --', flush=True)
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
                    mark = '  ❌ ЭКРАН ЧЁРНЫЙ — ВЫШЛА В DOS'
            print(f'  {i:3d} {k:12} экран {100 * black:5.1f}%  '
                  f'память={str(st["scene"]):14} {txt[:44]}{mark}', flush=True)
            if mark:
                print(f'\n❌ ВОСПРОИЗВЕДЕНО на нажатии {i} ({k}); память врёт: '
                      f'{S.identify(snap())["stack"]}', flush=True)
                break
        else:
            print('\n✅ последовательность пройдена, игра жива', flush=True)
        print(f'кадры: {OUT}', flush=True)
        raise SystemExit(0)

    dead = False
    for rep in range(int(os.environ.get('WW_REPEAT', '1'))):
        print(f'\n-- заход {rep + 1} --', flush=True)
        for j in range(int(os.environ.get('WW_AFTER', '16'))):
            press('return_key')
            sn = snap()
            st = S.identify(sn)
            stack = st.get('stack') or []
            txt = look(f'r{rep + 1}_after_{j + 1:02d}')
            print(f'  {j + 1:2d} {str(st["scene"]):16} стек={len(stack)}  {txt[:70]}', flush=True)
            if len(stack) < 2:
                print(f'❌ ИГРА УМЕРЛА на заходе {rep + 1}, добивка {j + 1}: стек={stack}',
                      flush=True)
                dead = True
                break
        if dead:
            break
        # сойти с клетки и вернуться: событие сработает заново
        back = (path[-1] + 2) % 4 if path else 2
        for side in (back, (back + 2) % 4):
            while facing != side:
                press('left')
                facing = (facing + 1) % 4
            press('up')
            w = S.where(snap())
            print(f'   шаг -> {(w["x"], w["y"])}', flush=True)
    print(f'кадры: {OUT}', flush=True)
