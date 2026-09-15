#!/usr/bin/env python3
"""Перерисовать надписи титульного меню: они в КАРТИНКЕ, а не в тексте.

    tools/titlemenu.py            # собрать en/ELFANN.GP4 и превью en/ELFANN.preview.png

`最初から始める / ロード1 / ロード2` на титульном экране не печатает ни один скрипт:
`START1.MES` только подсвечивает строку (`box-inv`) и ловит мышь, а сами надписи
нарисованы в `ELFANN.GP4` -- листе спрайтов, где каждая надпись отдельной плашкой.
Поэтому правка текста до них не дотягивается, и игрок видел японское меню в
полностью английской игре.

Как перерисовываем:
- надпись находим по цвету букв (индекс 7, белый) и стираем вместе с тенью
  (индекс 12, коричневый) до фона плашки (индекс 3) -- только внутри рамки надписи,
  рамку и дерево вокруг не трогаем;
- английскую пишем ШРИФТОМ ИГРЫ: ANK 8x16 из шрифтового ПЗУ PC-98, тем же, которым
  игра рисует английский текст в диалогах, -- с той же тенью на пиксель вправо-вниз;
- кодек GP4 -- `tools/juice/gp4`. Круг «GP4 -> BMP -> GP4» на этом файле сходится
  байт в байт (замер 2026-09-11), так что всё, что не надпись, остаётся нетронутым.

⚠️ Порядок надписей в листе снят глазами с кадра: левая колонка сверху вниз и
собранная панель справа. Если число найденных надписей не совпадёт с LABELS --
инструмент откажется, а не впишет не то не туда.
"""
import os, pathlib, shutil, subprocess, sys, tempfile
from collections import deque
import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import hdimage                                                       # noqa: E402
import gates                                                         # noqa: E402

GP4 = ROOT / 'tools/juice/gp4/gp4.rkt'
FONT = ROOT / 'emu/system/np2kai/font.bmp'   # np2kai: ANK 8x16, символ c в x = c*8, y 0..15
TEXT, SHADOW, PLATE = 7, 12, 3        # индексы снял счётом по пикселям плашки, не глазом

# сверху вниз: левая колонка (11 плашек), затем собранная панель справа (3)
LABELS = ['New Game', 'Load 1', 'Load 2', 'Load 3', 'Load 4', 'Load 5', 'Extras',
          'New Game', 'Load 1', 'Load 2', 'Main Menu',
          'New Game', 'Load 1', 'Load 2']


def glyphs():
    f = np.array(Image.open(FONT).convert('1'))
    return lambda ch: ~f[0:16, ord(ch) * 8: ord(ch) * 8 + 8]     # в ПЗУ буквы тёмные


def blobs(a):
    """Надписи: связные группы кремовых пикселей, склеенные с запасом по горизонтали."""
    m = a == TEXT
    grow = np.zeros_like(m)
    for dx in range(-6, 7):
        grow |= np.roll(m, dx, axis=1)
    seen = np.zeros_like(m); out = []
    for y, x in zip(*np.nonzero(grow)):
        if seen[y, x]:
            continue
        q = deque([(y, x)]); seen[y, x] = True; pts = []
        while q:
            cy, cx = q.popleft(); pts.append((cy, cx))
            for ny, nx in ((cy+1, cx), (cy-1, cx), (cy, cx+1), (cy, cx-1)):
                if 0 <= ny < m.shape[0] and 0 <= nx < m.shape[1] and grow[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True; q.append((ny, nx))
        ys = [p[0] for p in pts if m[p]]; xs = [p[1] for p in pts if m[p]]
        if ys and 10 <= max(ys) - min(ys) <= 18 and max(xs) - min(xs) >= 20:
            out.append((min(ys), max(ys), min(xs), max(xs)))
    # левая колонка раньше панели, внутри -- сверху вниз
    return sorted(out, key=lambda b: (b[2] > 200, b[0]))


def plate_span(a, y, x):
    x0 = x
    while x0 > 0 and a[y, x0 - 1] in (PLATE, TEXT, SHADOW):
        x0 -= 1
    x1 = x
    while x1 < a.shape[1] - 1 and a[y, x1 + 1] in (PLATE, TEXT, SHADOW):
        x1 += 1
    return x0, x1


def relabel(a):
    g = glyphs()
    found = blobs(a)
    if len(found) != len(LABELS):
        sys.exit(f'надписей найдено {len(found)}, ожидалось {len(LABELS)} -- лист не тот, '
                 f'что снят глазами; перерисовывать вслепую не буду')
    for (y0, y1, x0, x1), label in zip(found, LABELS):
        box = a[y0:y1 + 2, x0:x1 + 2]
        box[(box == TEXT) | (box == SHADOW)] = PLATE
        mid = (y0 + y1) // 2
        p0, p1 = plate_span(a, mid, (x0 + x1) // 2)
        w = len(label) * 8
        left = p0 + (p1 - p0 + 1 - w) // 2
        top = mid - 8
        for i, ch in enumerate(label):
            gl = g(ch)
            for yy, xx in zip(*np.nonzero(gl)):
                sy, sx = top + yy + 1, left + i * 8 + xx + 1
                if a[sy, sx] == PLATE:
                    a[sy, sx] = SHADOW
        for i, ch in enumerate(label):
            gl = g(ch)
            for yy, xx in zip(*np.nonzero(gl)):
                a[top + yy, left + i * 8 + xx] = TEXT
    return a


def racket(args, cwd):
    subprocess.run(['racket', str(GP4)] + args, cwd=cwd, check=True, capture_output=True)


def original_gp4(dest):
    """ELFANN.GP4 из НЕТРОНУТОГО образа -- никогда из уже переведённого."""
    d = pathlib.Path(tempfile.mkdtemp(prefix='wwtitle.'))
    cfg = d / 'mtoolsrc'
    hdimage.mtoolsrc(gates.BASE, cfg)
    env = os.environ | {'MTOOLSRC': str(cfg), 'MTOOLS_SKIP_CHECK': '1'}
    subprocess.run(['mcopy', '-o', 'z:/WW/ELFANN.GP4', str(dest)], env=env, check=True,
                   capture_output=True)
    shutil.rmtree(d, ignore_errors=True)


def build(out=ROOT / 'en/ELFANN.GP4'):
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        original_gp4(td / 'ELFANN.GP4')
        racket(['-d', '-f', 'ELFANN.GP4'], td)
        bmp = next(td.glob('ELFANN.GP4@*.bmp'))
        im = Image.open(bmp)
        a = relabel(np.array(im))
        new = Image.fromarray(a, 'P'); new.putpalette(im.getpalette())
        new.save(bmp)
        racket(['-e', '-f', bmp.name], td)
        shutil.copyfile(td / (bmp.name + '.gp4'), out)
        new.convert('RGB').resize((new.width * 2, new.height * 2), Image.NEAREST).save(
            out.with_suffix('.preview.png'))
    return out


if __name__ == '__main__':
    p = build()
    print(f'{p} ({p.stat().st_size} б), превью {p.with_suffix(".preview.png").name}')
