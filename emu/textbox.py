#!/usr/bin/env python3
"""What's WRITTEN in the message window -- glyph for glyph, from the frame, no model.

The grid is fixed, the font is a raster, so reading is bitmap comparison, not recognition:
either a cell matches a template exactly, or it isn't a letter.

⚠️ The game has its OWN font. The letter "A" raster from the message window is nowhere: not
in the PC-98 ROM (`system/np2kai/font.rom`), not in the emulator's `font.bmp`, not in the
disk image itself -- searched by direct raster, half-width, and OR-compression of full-width
16x16. So templates are derived from frames: `emu/learn_font.py` solves a substitution
cipher over all of the game's English text and drops the table into `textbox_glyphs.json`.

⚠️ The message text does NOT live in the PC-98 text layer. `emu/screen_text.py` read 0xA0000
out of a state snapshot and produced the same garbage on all six saved snapshots -- that
offset in a libretro snapshot isn't the text layer.

Geometry taken from frame `findings/0022.png` ("Astral were injured!!"):
  top-left of the first cell (96, 312), step 8x16, 56 columns, 4 rows.
  56*8 = 448 -> right edge 544, the window's black rectangle ends at 543. Checks out.
"""
import functools
import json
import pathlib

import numpy as np
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
STORE = HERE / "textbox_glyphs.json"

X0, Y0 = 96, 312          # Top-left of the first character cell
CW, CH = 8, 16            # character slot
COLS, ROWS = 56, 4        # message window
INK = 230                 # threshold is "hot" -- marker is drawn with color (248,252,248)
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
    """Window layout from left to right, top to bottom. None -- empty."""
    ink = _ink(frame)
    out = []
    for r in range(ROWS):
        for c in range(COLS):
            cell = ink[Y0 + r * CH: Y0 + (r + 1) * CH, X0 + c * CW: X0 + (c + 1) * CW]
            out.append(cell.tobytes() if cell.shape == (CH, CW) and cell.any() else None)
    return out


def lines(frame):
    """Message window lines as written. Unrecognized character is '\\uFFFD'."""
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
    """One-liner replica: the window shift is removed, as a person reads it."""
    return " ".join(l.strip() for l in lines(frame) if l.strip())


def empty(frame):
    return not any(cells(frame))


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:] or [str(HERE / "live.png")]:
        print(f"--- {p}")
        for l in lines(Image.open(p).convert("RGB")):
            print(f"   |{l}|")
