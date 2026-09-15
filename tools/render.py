#!/usr/bin/env python3
"""What the player will see in the message window -- computed, not guessed.

Line breaks are set by TWO mechanisms, and the trouble lives exactly at their seam:

1. **juice at compile time** (`engine/ai5/mes-compiler.rkt`, `text-wrap*`) wraps EACH
   string chunk SEPARATELY, FROM COLUMN ZERO, by `(wordwrap 46)`, inserting a hard line
   break. It skips right past the name insertion (`0`) and doesn't carry the column
   across it.
2. **the engine at render time** fills a window 56 wide and breaks at the edge wherever
   that lands -- Japanese doesn't need word wrapping.

Hence the typical defect: juice sees a 43-char chunk as short, but on screen it starts at
column 16, comes out to 59, and breaks at the edge -- an orphan word on its own line,
followed by juice's own hard break closing it off.

Both mechanisms are reproduced here from the compiler's code, so layout can be checked
without the emulator. Verified against a frame: `FLOOR05B`, Teshio's line -- 4 lines
character for character (`tools/render.py --selftest`).
"""
import re

import re

_MARK = re.compile(r'\{(\d+)\}')

WRAP = 46          # (wordwrap 46) from meta -- juice's threshold
WIDTH = 56         # message window width, taken from frame findings/0015.png
NAME = 6           # hero name length; in our saves Astral/Pollux are both 6
# ⚠️ We break ONE char short of the window edge. A line of exactly 56 chars the engine wraps
# ITSELF, and our explicit line break lands second -- an empty line shows up on screen
# (measured: 867 lines after the first layout version). One char of slack is cheaper than
# that pothole, and it also hedges in case the usable window width is really 55, not 56.
LINE = WIDTH - 1
# ⚠️ Padding ceiling. A row of fifty-odd spaces KILLS the game -- that's what crashed the
# save in the hero's room (STATUS.md §12). Doesn't fit the ceiling -- we don't insert the
# break at all.
MAX_PAD = 16


def _chop(words, w):
    """One juice pass: how many words fit the threshold. Word cost is len+1."""
    take, c = [], 0
    for s in words:
        n = len(s) + 1
        if w < c + n:
            break
        c += n
        take.append(s)
    return take, words[len(take):]


def wrap_chunk(s, w=WRAP):
    """`text-wrap*`: a string chunk -> chunks with line breaks inserted."""
    if not w:
        return [s]
    words = s.split(' ')
    if not words or w <= max(len(x) for x in words) + 1:
        return [s]                      # a long word -- juice doesn't touch the chunk at all
    rest, out = words, []
    while rest:
        take, rest = _chop(rest, (w // 2) * 2)
        if not take:                    # guard against looping forever
            out.append(' '.join(rest)); break
        out.append(' '.join(take))
    return [x + '\n' for x in out[:-1]] + [out[-1]]


def compile_parts(parts, w=WRAP):
    """What ends up in the .mes: strings already carrying juice's hard line breaks.

    `parts` are the elements of a (text …) form: strings and non-strings (name insertion).
    juice first merges adjacent strings that don't end in a line break.
    """
    merged, out = [], []
    for p in parts:
        if isinstance(p, str) and merged and isinstance(merged[-1], str) \
                and not merged[-1].endswith('\n'):
            merged[-1] += p
        else:
            merged.append(p)
    for p in merged:
        out.extend(wrap_chunk(p, w) if isinstance(p, str) else [p])
    return out


def screen(parts, name=NAME, w=WRAP, width=WIDTH, col0=0):
    """Lines on screen: first juice's compilation, then the engine filling the window.

    `col0` -- the column at which the form starts printing (see `tools/column.py`).
    The first line is shorter by `col0` accordingly -- that's exactly how the engine
    fills it.
    """
    text = ''.join('x' * name if not isinstance(p, str) else p
                   for p in compile_parts(parts, w))
    lines, col, cur = [], col0, ''
    for ch in text:
        if ch == '\n':
            lines.append(cur); cur, col = '', 0
            continue
        cur += ch; col += 1
        if col == width:
            lines.append(cur); cur, col = '', 0
    if cur:
        lines.append(cur)
    return lines


def layout_text(en, name=NAME, width=LINE, col0=0):
    """Flat text with {N} markers -> the same text with explicit line breaks.

    We track the column across the whole line: a name insertion is just a word of width
    `name`, and wraps as a whole. It used to be counted toward the column but NOT checked
    for wrapping, and ran off the edge: `...weaker than xxxxx` / `x` / `.` -- the name torn
    in half, the period on its own line (55 lines, measured 2026-09-11).

    We work on flat text rather than form elements because a break is often needed BEFORE
    an insertion -- i.e. at the end of the previous element. On a flat string that's just a
    position; with elements we'd have to reach backward.
    """
    def wide(tok):
        return len(_MARK.sub('X' * name, tok))

    # ⚠️ col0 is THE COLUMN AT WHICH THE FORM WILL START PRINTING. Not always zero: the
    # engine writes into one window continuously, and a form is often preceded by another
    # without a (wait) between them -- the speaker's name is assembled from three forms, the
    # direction is printed as a separate "Front. ". Layout didn't know this, the line ran
    # off the edge and the engine broke it MID-WORD (`tools/column.py`).
    out, col, fresh = [], col0, False     # fresh -- the line was just wrapped
    for word, sep in _words(en):
        n = wide(word)
        if col + n > width and col:
            while out and out[-1].isspace():
                out.pop()
            out.append('\n')
            col, fresh = 0, True
        if word:
            out.append(word); col += n; fresh = False
            if sep:
                out.append(sep); col += len(sep)
        elif not fresh:
            # ⚠️ A CHUNK'S LEADING SPACE IS SIGNIFICANT. A chunk often continues what a
            # neighboring form already printed: `(text " as many Healing Herbs…")`. Dropping
            # the space glues words together -- a "Manfound"-class defect that an agent
            # catches by eye. We only drop the space right after a wrap, where it would
            # become an indent.
            out.append(sep); col += len(sep)
    return ''.join(out)



def pad_breaks(s, name=NAME, width=WIDTH, max_pad=MAX_PAD, col0=0):
    """Replace explicit line breaks with space padding out to the window edge.

    ⚠️ Needed where a line carries NOT a name insertion but a `(number …)`. An explicit line
    break cuts the form in two, and the skeleton gets an extra `(TEXT )`; the gate collapses
    adjacent text forms, but only while there's no nested form inside them -- and
    `(number …)` is exactly that, nested. Padding, by contrast, changes ONLY the line
    content, which `skeleton()` zeroes out anyway, so the structure stays the same.

    ⚠️ The number's width is unknown: `(number …)` prints anywhere from one digit to five,
    and we count it as a name (`NAME`). The padding here is approximate -- but no worse than
    the old version, which also counted it as a name. There are 18 such lines in the whole
    game (measured 2026-09-11).
    """
    segs = s.split('\n')
    out, col = [], col0
    for i, seg in enumerate(segs):
        out.append(seg)
        col += len(_MARK.sub('X' * name, seg))
        if i < len(segs) - 1:
            need = width - col
            if 0 < need <= max_pad:
                out.append(' ' * need)
                col = 0
            else:
                col %= width          # padding didn't fit -- the engine will break it itself
    return ''.join(out)

def _words(s):
    """Words and the separator after each: ("Hello", " ") …"""
    out, i = [], 0
    while i < len(s):
        j = i
        while j < len(s) and s[j] != ' ':
            j += 1
        k = j
        while k < len(s) and s[k] == ' ':
            k += 1
        out.append((s[i:j], s[j:k]))
        i = k
    return out


# ---------------------------------------------------------------- layout audit
import pathlib, re, sys                                              # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from strings import forms                                            # noqa: E402
from strings import unescape                                         # noqa: E402



def parts_of(en):
    """Text with {N} markers -> elements of a (text …) form: strings and insertions."""
    out, i = [], 0
    for m in _MARK.finditer(en):
        if m.start() > i:
            out.append(en[i:m.start()])
        out.append(int(m.group(1)))
        i = m.end()
    if i < len(en):
        out.append(en[i:])
    return out


def flaws(lines, width=WIDTH, col0=0):
    """What's wrong here for the reader.

    ⚠️ `col0` is mandatory for the FIRST line: it's shorter by however much the form starts
    printing above zero. Without it a broken word went undetected -- the line
    "...for unauth" is 48 chars, not equal to the window width, but on screen it's exactly
    56 together with the "Front. " printed before it. That's how the defect slipped past
    the check.
    """
    bad = []
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        full = len(ln) + (col0 if i == 0 else 0)
        # ⚠️ A net for "overran by one cell to the right" (a player, 2026-09-13, a shop).
        # Measured from frames (`emu/textbox.py`, cross-checked here): cells run from x=96
        # in steps of 8, 56 columns, the last one ends at 543, the black window at 545. So
        # exactly 56 columns fit, and the 57th (544…551) already lands on the frame and
        # stays there after the window clears -- the engine draws that space as a black
        # glyph, not emptiness. `layout_text` breaks at LINE=55 and NEVER reaches the edge.
        # The only lines that hit exactly 56 are the ones `pad_breaks` padded with spaces up
        # to WIDTH; there are 19 of them, all shop money lines, and that's exactly where the
        # player sees the defect -- in shops.
        # ⚠️ WHAT ISN'T PROVEN: why the engine places a 57th cell if it breaks at the 56th.
        # For now the rule is a suspicion by coincidence of location, not a measured cause.
        if full > LINE:
            bad.append('line wider than text field')
        if full == width and nxt and ln[-1] != ' ' and nxt[:1] not in ('', ' '):
            bad.append('word broken')
        if re.search(r'\S {2,}\S', ln):
            bad.append('gap in line')
        # orphan: a short line after a full one, and it's not the end of the line
        prev = len(lines[i - 1]) + (col0 if i == 1 else 0) if i else 0
        if nxt is not None and i and prev >= width - 1 and len(ln.strip()) <= 12:
            bad.append('orphan word')
    return bad


def scan(en_dir):
    rows = []
    for f in sorted(pathlib.Path(en_dir).glob('*.MES.rkt')):
        if f.name.endswith('.orig.rkt'):
            continue
        src = f.read_text(encoding='utf-8')
        import column
        cols = column.columns(src)
        for form in forms(src):
            en = unescape(form['ja'])
            if any(ord(c) > 126 for c in en):
                continue                       # Japanese: no word wrap needed
            # ⚠️ w=0: juice's wrapping is GONE NOW -- `relayout.py` stripped `(wordwrap 46)`
            # from the meta of all 62 files (checked with `grep -l wordwrap en/*.MES.rkt` ->
            # empty). With the old threshold the audit counted a mechanism that no longer
            # applies and reported 26 "orphans" for no reason; the frames themselves show
            # none.
            c0 = cols.get(form['start'], 0)
            ls = screen(parts_of(en), w=0, col0=c0)
            bad = flaws(ls, col0=c0)
            if bad:
                rows.append((f.name[:-len('.rkt')], en, ls, sorted(set(bad))))
    return rows


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        p = ["[Teshio]: ", 0, ", is it because of that ring that you   and Sharon "
             "aren't getting along? ...They say Sharon     prefers a manly man."]
        want = ['[Teshio]: xxxxxx, is it because of that ring that you   ',
                'and',
                "Sharon aren't getting along? ...They say",
                'Sharon     prefers a manly man.']
        got = screen(p)
        assert got == want, f'simulator diverged from the frame:\n{got}\n{want}'
        print('✅ self-check: Teshio frame reproduced character by character')
    else:
        rows = scan(pathlib.Path(__file__).resolve().parent.parent / 'en')
        from collections import Counter
        c = Counter(k for *_, bad in rows for k in bad)
        print(f'lines with layout defects: {len(rows)}')
        for k, n in c.most_common():
            print(f'  {k}: {n}')
        for name, en, ls, bad in rows[:6]:
            print(f'\n--- {name}  [{", ".join(bad)}]')
            for l in ls:
                print(f'    |{l}|')
