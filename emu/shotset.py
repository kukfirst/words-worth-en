"""Снять набор кадров с ГОТОВОГО образа: загрузка, титульное меню, лавка, бой.

Зачем отдельно от зонда. `battle_probe.py` судит, жив ли образ, и снимает что попало по пути.
Для витрины нужны РАЗНЫЕ экраны с английским текстом, и снятые именно с того образа, который
уедет людям, -- иначе на картинке окажется текст прошлой сборки.

Что делает: холодный старт по рецепту `boot.RECIPE` с кадром после каждого шага, затем
маршрут `FLOOR05 -> SHP_5I` (лавка -- там живёт починенный вопрос о покупке), затем прогулка
до случайной встречи с кадром на каждое нажатие.

⚠️ По ОДНОМУ за раз: ядро пишет в системный каталог, а образ и кадры лежат в tmpfs рядом с
моделью. `WW_SYSTEM` даёт свою копию системного каталога, чтобы не задеть работающего агента.

    emu/.venv/bin/python emu/shotset.py <образ.hdi> <куда>
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
    """Кадр на диск + прочитанный с него текст окна -- чтобы выбирать по СОДЕРЖАНИЮ."""
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
    print('== холодный старт')
    run(600)
    # рецепт boot.RECIPE, но с кадром после каждого шага
    for label, key, then, _must in boot.RECIPE:
        if key is None:
            run(then)
        else:
            press(key, 8, then)
        snap_png(label.replace(': ', '_').replace(' ', '-').replace('/', '-'))
    print(f'  сцена после загрузки: {scene()}')

    # ⚠️ Новая игра начинается в КОМНАТЕ ГЕРОЯ (CAMP.MES), а маршрут в лавку записан от
    # коридора (FLOOR05.MES). Без первого звена второе не начинается, и зонд сорок шагов
    # топчется в комнате, докладывая «встречи не случилось».
    print('== в коридор и в лавку')
    # ⚠️ Маршрут, который не подходит к ТЕКУЩЕЙ сцене, -- не провал цепочки, а просто не
    # его очередь: после загрузки игра оказывается то в комнате героя, то уже в коридоре.
    # Раньше первое же «wrong place» обрывало путь, и лавка не снималась ни разу.
    all_routes = {x['name']: x for x in routes.load_all()}
    ok = False
    # ⚠️ Маршрут `FLOOR05 -> SHP_5I` отсюда НЕ ходит: сцена совпадает по имени, а точка старта
    # другая -- новая игра начинается в комнате героя, маршрут записан из коридора. Проверка
    # места по имени сцены этого не ловит, поэтому четырнадцать нажатий уводили героя в угол,
    # и прогулка после них не находила ни одной встречи (два прогона подряд). Не зовём.
    for name in ('CAMP.MES--to--FLOOR05.MES',):
        r = all_routes.get(name)
        if r is None:
            print(f'  нет маршрута {name}')
            continue
        ok, why = routes.replay(r, press, run, scene)
        print(f'  {name}: {ok} -- {why}')
        snap_png('route-' + name.split('--to--')[1].split('.')[0])
    if ok:
        # прилавок: подтвердить приветствие, войти в «Buy», пройти по списку предметов.
        # Ключи не угаданы: `space` -- подтверждение, `down` -- ход по списку. Кадр снимаем
        # на КАЖДОЕ нажатие, а нужные потом выбираются по прочитанному тексту.
        for i, k in enumerate(['space'] * 3 + ['space', 'down', 'space',
                                              'space', 'down', 'down', 'space'] * 2):
            press(k)
            snap_png(f'shop-{i:02d}-{k}')

    print('== прогулка до встречи')
    walk = ['up'] * 6 + ['right'] + ['up'] * 6 + ['left']
    for i in range(90):
        press(walk[i % len(walk)])
        sc = scene()
        if 'SENTO' in sc:
            print(f'  бой на шаге {i}: {sc}')
            for k in range(14):
                press('space')
                snap_png(f'fight-{k:02d}')
            break
    else:
        print('  встречи не случилось')
        snap_png('walk-end')

    # меню предметов: Escape в этой игре открывает именно его (см. STATUS §24б)
    print('== меню предметов')
    press('escape')
    snap_png('items-menu')
    press('down')
    snap_png('items-menu-2')

print(f'\nснято кадров: {len(shots)} -> {out}')
