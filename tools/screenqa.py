#!/usr/bin/env python3
"""Frame-based check: what is actually rendered in the message window, not what we think.

Why separate from `tools/relayout.py --check`. That check judges by the source and the layout
model. The model can be incomplete -- that is exactly how the defect «Front. The door has a sign:
'Prison, no entry for unauth / orized» slipped through: the form itself fit in the window, but it was
printed after someone else's «Front. » (`tools/column.py`). The source-based check did not see this because
it did not know about the column; the frame saw it immediately.

Here we read what is on screen: `emu/textbox.py` captures character cells as a raster, and catches

  * broken words -- a line fills the entire window and the next one starts with a letter;
  * gaps -- two or more spaces in the middle of a line;
  * unreadable character cells -- a raster that does not exist in the reference set.

Frames are taken from `emu/replay/` (the agent writes one on every human keypress), `emu/rec/` and
`emu/shots/`. ⚠️ This is NOT full coverage: only what we actually walked through is checked. The source
gate remains the primary one; this is the second, independent safety net.

    tools/screenqa.py              # all frames
    tools/screenqa.py --since 2h   # fresh ones only
"""
import argparse
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'emu'))
import textbox                                                       # noqa: E402

HOLE = re.compile(r'\S {2,}\S')
DIRS = ('replay', 'rec', 'shots', 'audit', 'findings')


def frames(since=None):
    for d in DIRS:
        for p in sorted((ROOT / 'emu' / d).glob('*.png')):
            if since and p.stat().st_mtime < since:
                continue
            yield p


def defects(lines, width):
    """Layout defects and, SEPARATELY, unreadable character positions.

    ⚠️ An unreadable position at the very END of the text -- not a defect, but a frame captured mid-print: the last
    glyph is half-drawn. The agent writes such a frame on every keypress, and without this
    caveat the entire report consists of them.
    ⚠️ An unreadable position in the MIDDLE -- almost always a digit 6..9, which are missing from the font
    references (`emu/textbox.py`). This is a gap in READING, not in the text, so the gate does not fail on it.
    """
    bad = []
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if len(ln) >= width and nxt and ln[-1:].strip() and nxt[:1].strip():
            bad.append('word broken')
        if HOLE.search(ln):
            bad.append('gap in the line')
    text = '\n'.join(lines).rstrip()
    return sorted(set(bad)), textbox.UNKNOWN in text[:-1]


def scan(since=None):
    from PIL import Image
    import textsrc
    idx = textsrc.index()
    seen, rows = set(), []
    for p in frames(since):
        try:
            im = Image.open(p).convert('RGB')
        except Exception:
            continue
        if im.size != (640, 400):
            continue
        lines = textbox.lines(im)
        if not any(l.strip() for l in lines):
            continue
        key = tuple(lines)
        if key in seen:
            continue
        seen.add(key)
        # ⚠️ A frame without a message window -- it's a FULL-SCREEN ILLUSTRATION, and its pixels in
        # window regions read as garbage: «hole in a line», «word is broken». Measurement
        # 2026-09-15: both "layout defects" out of the 289 screens turned out to be these images.
        # Sign: almost everything is unreadable. The actual utterance consists of signs, which
        # the reader knows; the image is one of those not present in any reference.
        txt = ''.join(lines)
        ink = [c for c in txt if c != ' ']
        if ink and sum(c == textbox.UNKNOWN for c in ink) / len(ink) > 0.5:
            continue
        bad, unread = defects(lines, textbox.COLS)
        if not bad and not unread:
            continue
        if unread:
            bad = bad + ['unreadable char position']
        try:
            src = textsrc.locate(lines, idx=idx)
        except Exception:
            src = None
        rows.append((p, lines, bad, src))
    return rows, len(seen)


def broken(rows):
    """Only genuine text defects -- that's what causes the gate to fail."""
    return [r for r in rows if any(b != 'unreadable char position' for b in r[2])]


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', help='only frames from the last N (h/m), e.g. 2h')
    a = ap.parse_args()
    since = None
    if a.since:
        mul = {'h': 3600, 'm': 60, 'd': 86400}[a.since[-1]]
        since = time.time() - float(a.since[:-1]) * mul
    rows, total = scan(since)
    hard = broken(rows)
    print(f'distinct screens read: {total}, with layout defects: {len(hard)}, '
          f'with unreadable cells: {len(rows) - len(hard)}')
    for p, lines, bad, src in rows:
        where = f"{src['file']}:{src['line']}" if src else 'not found in source'
        print(f"\n--- {p.name}  [{', '.join(bad)}]  -> {where}")
        for l in lines:
            print(f'    |{l}|')
    sys.exit(1 if hard else 0)
