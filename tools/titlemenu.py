#!/usr/bin/env python3
"""Redraw the title-menu labels: they're in the IMAGE, not in text.

    tools/titlemenu.py            # build en/ELFANN.GP4 and the en/ELFANN.preview.png preview

`最初から始める / ロード1 / ロード2` on the title screen isn't printed by any script:
`START1.MES` only highlights the line (`box-inv`) and catches the mouse, while the labels
themselves are drawn in `ELFANN.GP4` -- a sprite sheet where each label is its own tile.
So text edits don't reach them, and the player saw a Japanese menu in an otherwise fully
English game.

How we redraw:
- find the label by letter color (index 7, white) and erase it together with its shadow
  (index 12, brown) down to the tile background (index 3) -- only inside the label's frame,
  leaving the frame and the tree around it untouched;
- write the English in the GAME'S OWN FONT: ANK 8x16 from the PC-98 font ROM, the same one
  the game uses to draw English text in dialogue -- with the same one-pixel down-right shadow;
- GP4 codec -- `tools/juice/gp4`. The round trip "GP4 -> BMP -> GP4" on this file matches
  byte for byte (measured 2026-09-11), so anything that isn't a label stays untouched.

⚠️ The order of labels on the sheet was read off the frame by eye: the left column top to
bottom, then the assembled panel on the right. If the number of labels found doesn't match
LABELS -- the tool refuses rather than write the wrong thing in the wrong place.
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
FONT = ROOT / 'emu/system/np2kai/font.bmp'   # np2kai: ANK 8x16, character c at x = c*8, y 0..15
TEXT, SHADOW, PLATE = 7, 12, 3        # indices taken by counting the badge's pixels, not by eye

# top to bottom: left column (11 tiles), then assembled panel on the right (3)
LABELS = ['New Game', 'Load 1', 'Load 2', 'Load 3', 'Load 4', 'Load 5', 'Extras',
          'New Game', 'Load 1', 'Load 2', 'Main Menu',
          'New Game', 'Load 1', 'Load 2']


def glyphs():
    f = np.array(Image.open(FONT).convert('1'))
    return lambda ch: ~f[0:16, ord(ch) * 8: ord(ch) * 8 + 8]     # in ROM, characters are dark


def blobs(a):
    """Labels: connected groups of cream pixels, merged with a horizontal margin."""
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
    # left column before panel, inside -- top to bottom
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
        sys.exit(f'labels found {len(found)}, expected {len(LABELS)} -- not the sheet that '
                 f'was read off by eye; refusing to redraw blindly')
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
    """ELFANN.GP4 from a PRISTINE image -- never from an already translated one."""
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
    print(f'{p} ({p.stat().st_size} b), preview {p.with_suffix(".preview.png").name}')
