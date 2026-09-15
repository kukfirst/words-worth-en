#!/usr/bin/env python3
"""What COLUMN a line will start printing at -- the thing layout didn't know.

## What was wrong

`relayout.py` laid out every `(text …)` form starting from column zero. But the engine
prints into one window continuously, and a form is often preceded by another -- with no
`(wait)` between them:

    (cond ((== D 2) (<> (text "Front. ")))
          ((== D 1) (<> (text "Left. ")))
          ((== D 3) (<> (text "Right. ")))
          (else (<>)))
    (text "The door has a sign: 'Prison, no entry for unauthorized\\npersons.'")

The string on its own is 55 chars -- fits. But "Front. " shifts it by 7, coming to 62, and
the engine breaks at the window edge MID-WORD:

    Front. The door has a sign: 'Prison, no entry for unauth
    orized
    persons.'

⚠️ And the window balloons to fit the longest line, spilling past the frame -- that's what
the "black square" on screen actually is. One root, two symptoms.

## What we do

We compute the column from the script itself. `cond`/`if` branches are ALTERNATIVES, so we
take the max across them, not the sum: otherwise the three directions in the example above
would give 20 columns instead of 7, and the text would get narrowed for no reason.

⚠️ The width of `(number …)` is unknown until runtime -- counted at a ceiling of 5 chars.
⚠️ A name insertion is `NAME` chars wide (the player enters the name; Astral and Pollux are
both 6).
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from split import close, sexprs                                      # noqa: E402
from strings import forms, unescape                                  # noqa: E402

NAME = 6          # width of a name insertion
NUMBER = 5        # ceiling width of (number …)
# ⚠️ `(str …)` also prints into the message window: the battle line "<name> equipped
# 'STICK'!!" assembles the weapon name with it. I first threw it out, thinking it was only
# panel labels -- and the column for the tail "'!!" drifted five chars off. The real cause
# of columns past forty was something else: a chain of `(if …)` with the same condition
# was summing instead of selecting (see _chain).
PRINTS = ('text', 'str')
RESETS = ('wait', 'clear', 'menu-show', 'mes-call', 'mes-jump')

# ⚠️ The message window isn't only cleared by `(wait)`. Whole scenes are built on
# `(text …) (proc 28) (text …) (proc 28)`, where `proc 28` is "wait for a keypress and
# clear." Without knowing this, the column accumulated across the whole scene: the line
# "[Torturer 1]: Hmph…" came out to 45, layout decided there was no room, and broke THE
# NAME ITSELF -- "[Torturer" / "1]: Hmph…".
# The list isn't hand-entered: it's derived from `en/START.MES.rkt` -- a procedure clears
# the window if its body contains `(clear)`. Measured: that's 10, 25, 26, 28.
_RESET_PROCS = None


def reset_procs():
    """Procedure numbers that clear the message window -- from the bodies in START.MES."""
    global _RESET_PROCS
    if _RESET_PROCS is None:
        out = set()
        try:
            src = (pathlib.Path(__file__).resolve().parent.parent
                   / 'en/START.MES.rkt').read_text(encoding='utf-8')
            for m in re.finditer(r'\(define-proc (\d+)', src):
                a = m.start()
                if '(clear)' in src[a:close(src, a)]:
                    out.add(int(m.group(1)))
        except OSError:
            pass
        _RESET_PROCS = out
    return _RESET_PROCS
BRANCH = ('cond', 'if', 'if-else')
SEQ = ('<>', 'else', 'while', '<.>', 'mes', 'slot')
# ⚠️ A procedure's body prints from the column that was live AT THE CALL SITE, not where
# the procedure is declared. Counting it into the outer chain is nonsense: the column
# arrives from unrelated code, and `(text " were injured!!")` got broken for no reason.
# The body is counted from zero, and its column is never handed back outward.
PROC = 'define-proc'

_HEAD = re.compile(r'\(\s*([^\s()]+)')
_MARK = re.compile(r'\{(\d+)\}')


def head(src, a):
    m = _HEAD.match(src, a)
    return m.group(1) if m else ''


def printed(src, a, e):
    """What a (text …) / (str …) form will print: text with substitution markers."""
    fs = forms(src[a:e])
    if not fs:
        return '?' * NUMBER if '(number' in src[a:e] else ''
    out = _MARK.sub('x' * NAME, unescape(fs[0]['ja']))
    return out.replace('(number', '?' * NUMBER)


def walk(src, on_print):
    """Walk the file, calling `on_print(a, e, col)` on every printing form.

    The callback's return value is the TEXT the form actually prints. That's what lets
    layout run in ONE left-to-right pass: it returns the already-relaid text, and the next
    form's column is computed from that, not the old text.

    ⚠️ Without this, layout doesn't converge. Computing columns once, then relaying
    everything and recomputing, means running the system in circles: measured 233 defects
    after the first pass, 636 after the second, a crash on the third.
    """
    for a, e in sexprs(src, 0, len(src)):
        _walk(src, a, e, 0, None, on_print)
    return


def _walk(src, a, e, col, out, on_print=None):
    """The column after executing [a, e). Along the way, records each print's column in `out`.

    ⚠️ One pass per file, not a search per form: one pass per form would be 22,000 walks
    over the game's whole text -- minutes instead of a second.
    """
    h = head(src, a)
    if h in RESETS:
        return 0
    if h == 'proc':
        m = re.match(r'\(\s*proc\s+(\d+)', src[a:e])
        return 0 if m and int(m.group(1)) in reset_procs() else col
    if h in PRINTS:
        if out is not None:
            out[a] = col
        t = on_print(a, e, col) if on_print else printed(src, a, e)
        return len(t.rsplit('\n', 1)[1]) if '\n' in t else col + len(t)
    if h in BRANCH:
        # ⚠️ Branches are ALTERNATIVES: take the max, not the sum.
        # ⚠️ A `cond` branch looks like `((condition) (body))` -- it has NO opcode head, and
        # walking by head fell through it silently. We go into the branch the same way as
        # into a chain.
        best = col
        for s, x in sexprs(src, a + 1 + len(h), e - 1):
            best = max(best, _chain(src, s + 1, x - 1, col, out, on_print))
        return best
    if h == PROC:
        # ⚠️ A procedure body's text starting with a SPACE continues an already-printed
        # name: `(define-proc 43 (<> (text " were injured!!")))` prints right after the
        # hero's or enemy's name. We count that one from the name's width -- otherwise three
        # lines ("… attacked, trying to engulf …", "… swung their swords …") ran off the
        # window and broke mid-word.
        #
        # ⚠️⚠️ The name width is NOT the constant `NAME = 6`. In battle the name is printed
        # by `proc 41` from the same declaration group, and it's the ENEMY's name: from
        # "Delta" (5) to "A Suspicious Woman" (18). Measured three models (`procwidth2.py`):
        # `proc` = 0 found 19 defect cases, "own branch's name" found 85, "max over the
        # file" found 131. The first is blind, the third narrows text for nothing; the
        # second is correct, because `(define-proc 41 (text "Light Knight"))` sits right
        # next to `(define-proc 42 (text " raised their sword!!"))` -- the width is known
        # exactly.
        _chain(src, a + 1 + len(h), e - 1,
               _name_width(src, a) if _continues(src, a, e) else 0,
               out, on_print)
        return col
    if h in SEQ:
        return _chain(src, a + 1 + len(h), e - 1, col, out, on_print)
    return col                      # other opcodes print nothing


def _continues(src, a, e):
    """The procedure body starts with a space -- meaning it continues a printed name."""
    fs = forms(src[a:e])
    return bool(fs) and unescape(fs[0]['ja']).startswith(' ')


_NAMEPROC = re.compile(r'\(define-proc 41\b')


def _name_width(src, a):
    """Width of the name that will print BEFORE this procedure body.

    The name is declared by `(define-proc 41 (text "…"))` in the same group, so we take the
    nearest SUCH declaration ABOVE in the file. If there isn't one (a regular scene, not a
    battle), the engine substitutes the name from the save file, and that's `NAME` chars.
    """
    best = None
    for m in _NAMEPROC.finditer(src, 0, a):
        best = m.start()
    if best is None:
        return NAME
    fs = forms(src[best:close(src, best) + 1])
    if not fs:
        return NAME
    return max(NAME, len(unescape(fs[0]['ja'])))


_NUM = re.compile(r'\d+')


def _shape(src, a, e):
    """The condition's "shape": the same expression with numbers zeroed out.

    A game idiom -- a case split as a CHAIN of independent `(if …)` forms:

        (if (== (- V 3) 2) (<> (text "Gold Bar")))
        (if (== (- V 3) 3) (<> (text "Ascension Stone")))

    Structurally this is a sequence; by meaning, mutually exclusive branches. Their widths
    can't be summed: that's how battle files ran up a column of 45-53, layout decided there
    was no room, and broke the line early -- right down to breaking the speaker's NAME.
    """
    inner = sexprs(src, a + 1 + len(head(src, a)), e - 1)
    if not inner:
        return None
    c0, c1 = inner[0]
    return _NUM.sub('#', re.sub(r'\s+', ' ', src[c0:c1]))


def _chain(src, a, e, col, out, on_print=None):
    """Expressions in a row within [a, e): the column accumulates.

    ⚠️ Except for a chain of `(if …)` with the same condition shape -- that's a case split,
    and the max is taken across it, same as for `cond` branches.
    """
    cur = col
    spans = sexprs(src, a, e)
    i = 0
    while i < len(spans):
        s, x = spans[i]
        if head(src, s) == 'if':
            shape = _shape(src, s, x)
            j = i
            best = cur
            while (j < len(spans) and head(src, spans[j][0]) == 'if'
                   and shape is not None and _shape(src, *spans[j]) == shape):
                best = max(best, _walk(src, spans[j][0], spans[j][1], cur, out, on_print))
                j += 1
            if j > i:
                cur = best
                i = j
                continue
        cur = _walk(src, s, x, cur, out, on_print)
        i += 1
    return cur


def columns(src):
    """{form offset -> the column it starts printing at}."""
    out = {}
    for a, e in sexprs(src, 0, len(src)):
        _walk(src, a, e, 0, out)
    return out


def scan(en_dir):
    """[(file, offset, column)] for forms that do NOT start at zero."""
    rows = []
    for p in sorted(pathlib.Path(en_dir).glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        src = p.read_text(encoding='utf-8')
        cols = columns(src)
        for f in forms(src):
            c = cols.get(f['start'], 0)
            if c:
                rows.append((p.name, f['start'], c))
    return rows


if __name__ == '__main__':
    from collections import Counter
    root = pathlib.Path(__file__).resolve().parent.parent
    rows = scan(root / 'en')
    print(f'forms not starting at column zero: {len(rows)}')
    print('by column:', Counter(c for *_, c in rows).most_common(10))
