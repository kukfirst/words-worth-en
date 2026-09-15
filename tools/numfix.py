#!/usr/bin/env python3
"""Return a space where a number is printed flush against a word.

## What's broken

The number is printed by a SEPARATE instruction, right after the text:

    (text "[Dark Fortune Teller]: I see it!!... Your Speed is")
    (text (number (~ M 5)) "!!")

In Japanese this is correct -- `すばやさは` ends with a particle, and the number legitimately sticks to it.
In English you get `Your Speed is34!!`. Same thing in combat: `HP198 HP restored.`

⚠️ Why no gate caught this. The layout gate judges the FORM, and there are two here, and each
one is flawless on its own. `gate_edges` checks the character after the marker `{0}` -- there is no marker here.
The number doesn't even enter the form's text: `printed()` substitutes `?` in its place, and the seam
between the two instructions was never checked. Found by a player on a frame, 2026-09-15.

⚠️ Frames SAW this and it was read -- `|HP1?? was restored.|`, -- but `screenqa` only flagged
"unreadable character slot" (digits 6-9 not in font reference), and the stuck-together text next to it
went unnoticed. A report that hides a finding behind a neighboring complaint is a bad report.

## Where a space is NOT needed

If the cursor is repositioned between the text and the number (`set-arr~ @ 17` -- window row/column,
`@ 21` -- coordinates in the menu), then the number is printed at ITS OWN position, and a space would only
shift the column. This is the shop menu (`Healing Herb` … price) and the panel. Such places are left alone --
the criterion is taken from the code, not from a list of filenames.

    tools/numfix.py            # show
    tools/numfix.py --apply    # write and rebuild
"""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from split import close                                             # noqa: E402
import gates                                                        # noqa: E402

EN = ROOT / 'en'

# ⚠️ Space is MISSING. Inserting it into `"HP" + N + " HP restored."` means to get
# "HP 198 HP restored." has HP doubled, while in `"…'s HP" + N + " point recovered!!"` --
# «…'s HP 8 point recovered!!». Japanese puts the number between the particles, English doesn't
# it can; the phrase must be REBUILT around the number. Pairs of "what was before the number / what was after" ->
# "what will become". The list is not made up: it was derived by recomputing all 97 points.
PHRASES = {
    ('HP', ' was restored.'):            ('HP restored by ', '.'),
    ('HP', ' HP restored.'):             ('HP restored by ', '.'),
    ('HP was', ' restored.'):            ('HP restored by ', '.'),
    ('HP was', ' recovered.'):           ('HP restored by ', '.'),
    ('HP of', ' was restored.'):         ('HP restored by ', '.'),
    ('HP by', ' was restored.'):         ('HP restored by ', '.'),
    ('HP by', ' HP restored.'):          ('HP restored by ', '.'),
    ('HP', ' was recovered.'):           ('HP restored by ', '.'),
    ('HP', ' recovered some HP.'):       ('HP restored by ', '.'),
    ('HP', "'s HP was restored."):       ('HP restored by ', '.'),
    ("The Saint of Light's HP", ' point recovered!!'):
        ("The Saint of Light's HP restored by ", '!!'),
    ("The Female Light Saint's HP", ' recovered a point!!'):
        ("The Female Light Saint's HP restored by ", '!!'),
    ("The Woman in Black's HP", ' point recovered!!'):
        ("The Woman in Black's HP restored by ", '!!'),
    ("The Dark Messenger's HP", ' point recovered!!'):
        ("The Dark Messenger's HP restored by ", '!!'),
    ("The Black-robed Monk's HP", ' point recovered!!'):
        ("The Black-robed Monk's HP restored by ", '!!'),
    ("The Shadow Saint's HP", ' point recovered!!'):
        ("The Shadow Saint's HP restored by ", '!!'),
}

# Cursor repositioned -> the number goes into its own column, nothing to merge with.
MOVED = re.compile(r'set-arr~ @ (?:17|21)\b')
# Print interrupted -> the number will start a new line or a new window.
BREAKS = re.compile(r'\(wait|\(clear|proc 28\b|proc 10\b')


def sites(src):
    """[(span of the literal before the number, its text, span of the literal after the number, its text)]."""
    spans = [(m.start(), close(src, m.start()) + 1) for m in re.finditer(r'\(text\b', src)]
    out = []
    for (a, e), (na, ne) in zip(spans, spans[1:]):
        if not re.match(r'\(text\s*\(number', src[na:ne]):
            continue
        between = src[e:na]
        if MOVED.search(between) or BREAKS.search(between):
            continue
        lits = list(re.finditer(r'"((?:[^"\\]|\\.)*)"', src[a:e]))
        if not lits:
            continue
        last = lits[-1]
        tail = last.group(1)
        if not tail or tail.endswith((' ', '\\n')):
            continue
        aft = list(re.finditer(r'"((?:[^"\\]|\\.)*)"', src[na:ne]))
        head = aft[0].group(1) if aft else ''
        out.append(((a + last.start(1), a + last.end(1), tail),
                    ((na + aft[0].start(1), na + aft[0].end(1), head) if aft else None)))
    return out


def rewrite(tail, head):
    """How to rewrite a pair around a number. None -- one space is enough."""
    key = (tail.strip(), head)
    if key in PHRASES:
        return PHRASES[key]
    return None


def main(names, apply):
    total, touched = 0, []
    for name in names:
        src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
        rows = sites(src)
        if not rows:
            continue
        total += len(rows)
        touched.append(name)
        edits = []
        for (ba, be, tail), aft in rows:
            new = rewrite(tail, aft[2] if aft else '')
            if new and aft:
                edits.append((ba, be, new[0]))
                edits.append((aft[0], aft[1], new[1]))
                shown = f'{new[0]!r} + number + {new[1]!r}'
            else:
                edits.append((ba, be, tail + ' '))
                shown = f'{tail[-30:] + " "!r} + number'
            if len(touched) <= 4 and len(edits) <= 4:
                print(f'  {name:16} …{tail[-30:]!r} + number  ->  {shown}')
        if apply:
            for a2, b2, txt in sorted(edits, reverse=True):
                src = src[:a2] + txt + src[b2:]
            (EN / f'{name}.rkt').write_text(src, encoding='utf-8')
    print(f'\nspots: {total}, files: {len(touched)}')
    if not apply:
        print('NOT WRITTEN. Apply: tools/numfix.py --apply')
        return 0
    print('\nrebuild:')
    bad = 0
    for name in touched:
        gates.juice(['-cf', f'{name}.rkt'], EN)
        mes = EN / f'{name}.rkt.mes'
        n = mes.stat().st_size if mes.exists() else 0
        over = '⚠️ OVER THRESHOLD' if n > gates.MES_MAX else ''
        if not n or over:
            bad += 1
            print(f'  {name:16} {n} b{over}', flush=True)
    print(f'{"❌ failures:" + str(bad) if bad else "✅ all rebuilt under threshold"}')
    return 1 if bad else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    ns = a.names or sorted(p.name[:-4] for p in EN.glob('*.MES.rkt')
                           if not p.name.endswith('.orig.rkt'))
    sys.exit(main(ns, a.apply))
