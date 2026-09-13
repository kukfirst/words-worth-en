"""Дойти до боя без модели и сказать, пережил ли его образ.

Правка цифр убивала игру ровно в момент загрузки SENTO00.MES (начало боя), а мелкий зонд
(ab_probe: одно нажатие `up`) до боя не доходит и потому всё подряд объявляет живым.
Здесь ходим по коридору, пока сцена не станет боевой, и только тогда выносим вердикт.

    emu/.venv/bin/python emu/battle_probe.py <образ в scratch/emuprobe>

⚠️ По ОДНОМУ за раз. /tmp здесь tmpfs, то есть образ (20 МБ) и кадры лежат в оперативке
рядом с моделью llama.cpp (~35 ГБ), и два зонда параллельно уже роняли фоновые задачи по
нехватке памяти. Образы после проверки удалять.
"""
import os, pathlib, sys
import numpy as np
from libretro import Session
from libretro.drivers import ExplicitPathDriver, IterableInputDriver
from libretro.api.input.keyboard import KeyboardState
from libretro.api.input.joypad import JoypadState

# ⚠️ Корень ищется относительно ЭТОГО файла, а не вбит абсолютным путём: зонд запускают и
# из копии репозитория, и из чужого клона.
EMU = pathlib.Path(__file__).resolve().parent; WW = EMU.parent
# ⚠️ Каталог зонда НЕ привязан к сессии агента. Раньше сюда был вшит путь одного конкретного
# scratchpad, и при запуске из другой сессии зонд молча ничего не находил.
SCR = pathlib.Path(os.environ.get('WW_SCRATCH') or '/tmp/ww-probe')
SCR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(EMU)); import state as S, boot                # noqa: E402
CORE = "/usr/lib/libretro/np2kai_libretro.so"
# ⚠️ Ядро ПИШЕТ в системный каталог (np2kai.cfg). Зонд рядом с работающим агентом делит с
# ним этот файл -- подозреваемый в смерти агента 2026-09-10. WW_SYSTEM даёт зонду свою копию.
SYSTEM = pathlib.Path(os.environ.get("WW_SYSTEM") or (EMU / "system"))
img = sys.argv[1]
tag = pathlib.Path(img).stem   # ⚠️ имя кадра — из stem, не из пути: полный путь внутри имени файла зонд роняет
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

# ⚠️ Встречи случайны (счётчик seed'ится таймером), поэтому «за 60 шагов боя не было» — это
# НЕ «образ жив», это «не проверено». Ходим, пока бой не случится, и только тогда судим.
# нажатие направления сперва ПОВОРАЧИВАЕТ, и маршрут из одних поворотов крутится
# на месте: идём в основном вперёд, сворачивая раз в шесть шагов
WALK = ['up'] * 6 + ['right'] + ['up'] * 6 + ['left']
with sess:
    run(600)
    ok, _ = boot.drive(press, run, frame, log=lambda m: None)
    print(f'{img}: загрузка ok={ok} сцена={S.identify(snap())["scene"]}', flush=True)
    seen_fight = False
    for i in range(LIMIT):
        press(WALK[i % len(WALK)])
        sc = str(S.identify(snap())['scene'])
        h = health()
        if h['class'] not in ('idle', 'busy'):
            print(f'{img}: УМЕР на шаге {i} ({h["class"]}), сцена {sc}, бой был={seen_fight}')
            f = frame()
            if f is not None:
                from PIL import Image
                Image.fromarray(f).save(SCR / f'died_{tag}.png')
            break
        if 'SENTO' in sc:
            if not seen_fight:
                print(f'{img}: бой на шаге {i}, сцена {sc}', flush=True)
            seen_fight = True
            from PIL import Image
            for k in range(30):                     # отбиваемся до конца боя и снимаем каждый кадр
                press('space')
                f = frame()
                if f is not None:
                    Image.fromarray(f).save(SCR / f'fight_{tag}_{k:02d}.png')
            h = health()
            if h['class'] not in ('idle', 'busy'):
                print(f'{img}: УМЕР В БОЮ ({h["class"]}), сцена {S.identify(snap())["scene"]}')
                from PIL import Image
                f = frame()
                if f is not None:
                    Image.fromarray(f).save(SCR / f'died_{tag}.png')
                break
            print(f'{img}: БОЙ ПЕРЕЖИТ на шаге {i}, stats={S.stats(snap())}')
            break
    else:
        print(f'{img}: {LIMIT} шагов без боя — НЕ ПРОВЕРЕНО, stats={S.stats(snap())}')
    from PIL import Image
    f = frame()
    if f is not None:
        Image.fromarray(f).save(SCR / f'end_{tag}.png')
