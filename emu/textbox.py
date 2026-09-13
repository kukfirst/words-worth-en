#!/usr/bin/env python3
"""Что НАПИСАНО в окне сообщения -- знак в знак, из кадра, без модели.

Сетка жёсткая, шрифт растровый, поэтому чтение -- сравнение битовых карт, а не
распознавание: либо знакоместо совпало с эталоном ровно, либо это не буква.

⚠️ Шрифт у игры СВОЙ. В ПЗУ PC-98 (`system/np2kai/font.rom`), в `font.bmp` эмулятора и в
самом образе диска растра буквы «A» из окна сообщения НЕТ -- искал прямым растром,
полушириной и OR-сжатием полноширинных 16x16. Поэтому эталоны выведены из кадров:
`emu/learn_font.py` решает подстановочный шифр по всему английскому тексту игры и
кладёт таблицу в `textbox_glyphs.json`.

⚠️ Текст сообщения НЕ лежит в текстовом слое PC-98. `emu/screen_text.py` читал 0xA0000
из снимка состояния и на всех шести сохранённых снимках выдавал одно и то же месиво --
по этому смещению в снимке libretro лежит не текстовый слой.

Геометрия снята с кадра `findings/0022.png` («Astral were injured!!»):
  левый верх первого знакоместа (96, 312), шаг 8x16, 56 колонок, 4 строки.
  56*8 = 448 -> правый край 544, чёрный прямоугольник окна кончается на 543. Сходится.
"""
import functools
import json
import pathlib

import numpy as np
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
STORE = HERE / "textbox_glyphs.json"

X0, Y0 = 96, 312          # левый верх первого знакоместа
CW, CH = 8, 16            # знакоместо
COLS, ROWS = 56, 4        # окно сообщения
INK = 230                 # порог «горит» -- знак рисуется цветом (248,252,248)
UNKNOWN = "�"


@functools.lru_cache(maxsize=1)
def templates():
    try:
        return {bytes.fromhex(k): v for k, v in json.loads(STORE.read_text()).items()}
    except Exception:
        return {}


def _ink(frame):
    a = np.asarray(frame)
    if a.ndim == 3:
        return (a[:, :, 0] >= INK) & (a[:, :, 1] >= INK) & (a[:, :, 2] >= INK)
    return a >= INK


def cells(frame):
    """Знакоместа окна слева направо, сверху вниз. None -- пустое."""
    ink = _ink(frame)
    out = []
    for r in range(ROWS):
        for c in range(COLS):
            cell = ink[Y0 + r * CH: Y0 + (r + 1) * CH, X0 + c * CW: X0 + (c + 1) * CW]
            out.append(cell.tobytes() if cell.shape == (CH, CW) and cell.any() else None)
    return out


def lines(frame):
    """Строки окна сообщения как они написаны. Нераспознанное знакоместо -- '\\uFFFD'."""
    tpl = templates()
    cs = cells(frame)
    out = []
    for r in range(ROWS):
        row = cs[r * COLS:(r + 1) * COLS]
        out.append("".join(" " if s is None else tpl.get(s, UNKNOWN) for s in row).rstrip())
    while out and not out[-1]:
        out.pop()
    return out


def text(frame):
    """Реплика одной строкой: перенос окна убран, как её читает человек."""
    return " ".join(l.strip() for l in lines(frame) if l.strip())


def empty(frame):
    return not any(cells(frame))


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:] or [str(HERE / "live.png")]:
        print(f"--- {p}")
        for l in lines(Image.open(p).convert("RGB")):
            print(f"   |{l}|")
