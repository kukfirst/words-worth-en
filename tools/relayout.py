#!/usr/bin/env python3
"""Lay out lines ourselves -- instead of letting two wrapping mechanisms argue.

## What was wrong

TWO things inserted line breaks. juice, at compile time, cut each string chunk separately
starting from column zero via `(wordwrap 46)`, skipping over the name substitution; the
engine, at render time, filled a 56-wide window and tore at the edge. `wrapfix.py` fixed the
second with space padding, but didn't know about the first -- producing orphan words on their
own line and gaps in the middle of a line:

    [Teshio]: Astral, is it because of that ring that you
    and
    Sharon aren't getting along? ...They say
    Sharon     prefers a manly man.

Measured with the simulator (`tools/render.py`, checked against the frame sign for sign):
**605 lines** with defects -- 451 gaps, 172 orphans, 32 torn words.

## What we do

We strip `(wordwrap …)` from meta -- the compiler stops cutting on its own -- and insert line
breaks explicitly, tracking the column across the whole line together with the name
substitution. Space padding is no longer needed and is dropped.

⚠️ The name's length is only known in-game, the player enters it. We compute against NAME=6
(that's the length of Astral and Pollux). For a name of a different length the layout will
shift -- but no worse than before.

    tools/relayout.py            # measure
    tools/relayout.py --apply    # write
"""
import argparse, pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from strings import forms, patch, split_translation, unescape, escape
from render import layout_text, pad_breaks, parts_of, screen, flaws, NAME, WIDTH, _MARK
import column

ROOT = pathlib.Path(__file__).resolve().parent.parent
EN = ROOT / 'en'
META_WRAP = re.compile(r'\s*\(wordwrap\s+\d+\)')


def relaid(en, padded=False, col0=0):
    """Text with markers -> same text with line breaks inserted.

    `padded` -- use padding instead of explicit newlines: we do this where a line
    carries `(number …)`, otherwise the form is split in two and the gate sees an extra `(TEXT )`.
    `col0` -- the column from which the form starts printing (`tools/column.py`).
    """
    # ⚠️ A LEADING newline is a line break the author chose, not a wrap: it starts the tag on
    # a fresh line after "That's 120 gold." Flattening it to a space let the layout break
    # inside the tag instead ("[Armor Shop\nOwner]", 4 lines in the shops, 2026-09-17).
    lead = len(en) - len(en.lstrip('\n'))
    if lead:
        return en[:lead] + relaid(en[lead:], padded, col0=0)
    flat = re.sub(r'  +', ' ', en.replace('\n', ' '))
    out = layout_text(flat, col0=col0)
    return pad_breaks(out, col0=col0) if padded else out


def fix_file(name, apply=False):
    """Rewrite the file in a SINGLE left-to-right pass.

    ⚠️ Each form's column is computed from the ALREADY rewritten text of the preceding ones -- otherwise
    the system does not converge: layout changes width, width changes columns, columns change
    layout. Measured on a two-pass approach ("compute all columns, then rewrite everything"):
    233 defects after the first pass, 636 after the second, crash on the third.
    """
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    out = META_WRAP.sub('', src, count=1)
    bystart = {f['start']: f for f in forms(out)}
    edits, touched = [], 0

    def on_print(a, e, col):
        f = bystart.get(a)
        if not f:
            return column.printed(out, a, e)
        en = unescape(f['ja'])
        if any(ord(c) > 126 for c in en):
            return column.printed(out, a, e)     # Japanese: word wrapping not needed
        # name substitution -- this is `0`/`1`; everything else (`(number …)`) requires follow-up
        new = relaid(en, padded=any(not i.isdigit() for i in f['ins']), col0=col)
        if new != en:
            pieces = split_translation(new, len(f['slots']), len(f['ins']),
                                       f['lead'], f['trail'])
            if pieces is not None:
                for (x, y), piece in zip(f['slots'], pieces):
                    edits.append((x, y, escape(piece)))
                nonlocal touched
                touched += 1
            else:
                new = en
        return column._MARK.sub('x' * column.NAME, new)

    column.walk(out, on_print)
    out = patch(out, edits) if edits else out
    if apply:
        (EN / f'{name}.rkt').write_text(out, encoding='utf-8')
    return touched, len(out) - len(src)


def check():
    """Layout invariant: no holes, no orphans, no broken words, no runs of spaces.

    Replaces the old `wrapfix.py --check`. A run of 2+ spaces is allowed ONLY as padding,
    i.e. only if it runs flush against the edge of the window: padding kept its one narrow role — in
    lines with `(number …)`, where an explicit line break would split the form in two (`pad_breaks`).
    Plus the layout itself is cross-checked with the `tools/render.py` simulator against a verified frame.

    ⚠️ This class of defect is not judged by eye: in a 640x400 bitmap font one space from
    two is indistinguishable, and the QA agent reported double spaces three times that are not
    on the frame (findings 0016, 0017, 0020). So the check is done against the source and by count.
    """
    bad, residue, lost = [], [], []
    for p in sorted(EN.glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        src = p.read_text(encoding='utf-8')
        cols = column.columns(src)
        for f in forms(src):
            en = unescape(f['ja'])
            if any(ord(c) > 126 for c in en):
                continue
            c0 = cols.get(f['start'], 0)
            # ⚠️ COLUMN EXCEEDS WINDOW WIDTH -- model lost the cursor, nothing to judge by.
            # This is how it appears where the cursor is placed by COORDINATES, not by printing: screen
            # keyboard in NAME.MES draws rows of letters via (set-arr~ @ 17 x y), and column
            # reaches 74 with a window of 56. Treating `@ 17` as a line break would be more honest,
            # but this changes the column for 169 forms in 29 files -- the cost is incomparable to a single one
            # false complaint. Check 2026-09-13: this form appears EXACTLY ONCE in the game.
            if c0 >= WIDTH:
                lost.append((p.name[:-4], en[:40]))
                continue
            why = flaws(screen(parts_of(en), w=None, col0=c0), col0=c0)
            # ⚠️ REMAINDER that can't be reallocated: the form starts with a substitution
            # (`(text (number …) "!!")`), there is no string piece before it, and put it there
            # the line break goes nowhere. The break falls at the junction of the substitution and the tail -- this
            # Ugly, but not a broken word. Five such across the entire game (measured 2026-09-11).
            if why and f['lead'] and en.lstrip().startswith('{'):
                residue.append((p.name[:-4], en[:40]))
                continue
            flat = en.replace('\n', ' ')
            for m in re.finditer(r'\S( {2,})\S', flat):
                col = len(_MARK.sub('X' * NAME, flat[:m.end() - 1])) % WIDTH
                if col:                   # row didn't reach the edge of the window -- so it's not a finisher
                    why.append('line of spaces not at the edge of the window')
            if why:
                bad.append((p.name[:-4], sorted(set(why)), en[:60]))
    print(f'lines with layout defects: {len(bad)}')
    for n, why, t in bad[:8]:
        print(f'  {n:16} {", ".join(why)}: {t}')
    if residue:
        print(f'  (plus {len(residue)} forms starting with a substitution -- nowhere '
              f'to put the break: {", ".join(n for n, _ in residue)})')
    if lost:
        print(f'  (plus {len(lost)} forms where the cursor is placed by coordinates and the '
              f'model column exceeds the window: {", ".join(n for n, _ in lost)})')
    return len(bad)


def names():
    return sorted(p.name[:-4] for p in EN.glob('*.MES.rkt')
                  if not p.name.endswith('.orig.rkt'))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('name', nargs='?')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--check', action='store_true', help='Verify layout invariant')
    a = ap.parse_args()
    if a.check:
        sys.exit(1 if check() else 0)
    tot = grew = 0
    for n in ([a.name] if a.name else names()):
        t, g = fix_file(n, a.apply)
        if t:
            print(f'  {n:16} relaid {t:4d}, {g:+d} chars', flush=True)
        tot += t; grew += g
    print(f'\ntotal: relaid {tot} lines, {grew:+d} chars')
    if not a.apply:
        print('NOT WRITTEN. Apply: tools/relayout.py --apply')
