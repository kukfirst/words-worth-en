#!/usr/bin/env python3
"""Translation units for juice .rkt sources.

A unit is a WHOLE (text ...) form, not a single string literal. A form may hold
several string slots separated by runtime insertions -- `(text "[" 0 "]: hi")`
is the hero's name spliced into a line. Translating the slots separately hands
the model fragments like `さんか。` (an honorific with nothing to attach to),
so the slots are joined into one sentence with {0}-style markers standing in
for the insertions, and split apart again on the way back.

Byte spans are recorded per slot, so re-insertion never rematches text.
"""
import json, pathlib, re, sys

_MARK = re.compile(r'\{(\d+)\}')


def _elements(src, open_paren):
    """Split one (text ...) form into elements. Returns (elements, end_index).

    Each element is ('str', start, end, value) or ('raw', text).
    """
    i = open_paren + 1
    n = len(src)
    depth = 1
    els = []
    buf = []
    while i < n:
        c = src[i]
        if c == '"':
            j = i + 1
            while j < n:
                if src[j] == '\\':
                    j += 2
                    continue
                if src[j] == '"':
                    break
                j += 1
            if buf:
                els.append(('raw', ''.join(buf)))
                buf = []
            els.append(('str', i + 1, j, src[i+1:j]))
            i = j + 1
            continue
        if c == ';':
            k = src.find('\n', i)
            i = n if k < 0 else k + 1
            continue
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                if buf:
                    els.append(('raw', ''.join(buf)))
                return els, i
        buf.append(c)
        i += 1
    return els, n


def forms(src):
    """Yield one dict per (text ...) form that contains at least one string.

    ⚠️ An insertion BEFORE the first string (or after the last) is part of the line
    too: `(text 0 "は『石板のかけら』を手に入れた！！")` prints the hero's name and then
    the sentence, with nothing between them. Trimming it -- which this did until
    2026-09-08 -- handed the model a subject-less predicate, it translated a whole
    sentence, and the game rendered «AstralYou got a Stone Tablet Fragment!!».
    Measured blast radius at the time: 345 lines. So edge insertions get a {N}
    marker like any other, and `lead`/`trail` say the marker sits at an edge (there
    is no string slot there, so `split_translation` has to drop the empty piece).
    """
    out = []
    for m in re.finditer(r'\(text\b', src):
        els, end = _elements(src, src.index('(', m.start()))
        if not any(e[0] == 'str' for e in els):
            continue
        body = list(els)
        if body and body[0][0] == 'raw':          # strip the `text` keyword itself
            head = re.sub(r'^\s*text\b', '', body[0][1])
            body[0] = ('raw', head)
        slots, parts, ins, k = [], [], [], 0
        for e in body:
            if e[0] == 'str':
                slots.append((e[1], e[2]))
                parts.append(e[3])
            else:
                txt = e[1].strip()
                if not txt:
                    continue
                parts.append('{%d}' % k)
                ins.append(txt)
                k += 1
        first_str = next(i for i, x in enumerate(parts) if not _MARK.fullmatch(x))
        last_str = len(parts) - 1 - next(i for i, x in enumerate(reversed(parts))
                                         if not _MARK.fullmatch(x))
        out.append({'start': m.start(), 'end': end + 1,
                    'slots': slots, 'ins': ins, 'ja': ''.join(parts),
                    'lead': first_str, 'trail': len(parts) - 1 - last_str})
    return out


def split_translation(en, nslots, nins, lead=0, trail=0):
    """Split a translated line back into per-slot pieces. None if markers are wrong.

    `lead`/`trail` count markers that sit outside every string slot (see `forms`):
    the model must still write them, but they leave an empty piece with no span to
    write it to, and anything non-empty there would be text the game never shows.
    """
    found = _MARK.findall(en)
    if len(found) != nins or [int(x) for x in found] != list(range(nins)):
        return None
    pieces = _MARK.split(en)
    # re.split with a group gives [text, num, text, num, text...]
    texts = pieces[0::2]
    for _ in range(lead):
        if not texts or texts[0].strip():
            return None
        texts = texts[1:]
    for _ in range(trail):
        if not texts or texts[-1].strip():
            return None
        texts = texts[:-1]
    return texts if len(texts) == nslots else None


def rebuild_dict(src):
    """Swap the literal (dict …) for (dict-build).

    The original dictionary holds Japanese glyphs, so English text falls back to
    two-byte SJIS and the file doubles in size (END3: 30 297 vs 15 200 bytes).
    Regenerating it from the translated text brought that to 19 642. Note the
    form spans lines and the first '(dict' in the file is actually (dictbase N).
    """
    m = re.search(r'\(dict\s+#', src)
    if not m:
        return src
    i = m.start()
    depth = 0
    for j in range(i, len(src)):
        if src[j] == '(':
            depth += 1
        elif src[j] == ')':
            depth -= 1
            if depth == 0:
                return src[:i] + '(dict-build)' + src[j+1:]
    return src


def unescape(s):
    """Unescape the source: on screen \\" is a single quote, not two.

    ⚠️ forms() returns the RAW literal text, backslashes included, while patch()
    re-escapes. Feeding it already-escaped text means ending up with \\\\"
    instead of \\" and showing the player extra backslashes: that's exactly what I did in v1,
    corrupting 9 lines in 8 files. The column count was also wrong: \\" was counted as two characters.

    ⚠️ `\\n` is a LINE BREAK, not the letter `n`. juice writes breaks this way, and
    tools/relayout.py inserts them itself; naively stripping the slash would turn a break into a letter
    mid-word on every re-read.
    """
    out, i = [], 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            out.append('\n' if s[i + 1] == 'n' else s[i + 1]); i += 2
        else:
            out.append(s[i]); i += 1
    return ''.join(out)


def escape(s):
    """Inverse of unescape: text -> body of a string literal."""
    return (s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n'))


def patch(src, edits):
    """edits: (start, end, new_value) over string-literal spans, applied right-to-left."""
    for start, end, val in sorted(edits, key=lambda e: -e[0]):
        src = src[:start] + val + src[end:]
    return src


# kept for callers that only want raw literals
def scan(src):
    return [(s, e, src[s:e]) for f in forms(src) for (s, e) in f['slots']]


if __name__ == '__main__':
    if sys.argv[1] == 'dump':
        units = []
        for p in sorted(pathlib.Path(sys.argv[2]).glob('*.MES.rkt')):
            src = p.read_text(encoding='utf-8')
            for f in forms(src):
                units.append({'file': p.name, 'slots': f['slots'],
                              'ins': f['ins'], 'ja': f['ja'],
                              'lead': f['lead'], 'trail': f['trail']})
        json.dump(units, open(sys.argv[3], 'w'), ensure_ascii=False, indent=0)
        multi = sum(1 for u in units if len(u['slots']) > 1)
        print(f'{len(units)} units ({multi} with a name insertion) '
              f'from {len(set(u["file"] for u in units))} files -> {sys.argv[3]}')

# --- digits in the message window -----------------------------------------------------------
# The engine prints (number …) as two-byte codes, and in half-width font on the English build
# they come out as half-glyphs («dealt :( damage!!»). Benchmark (tools/number_probe.py, one
# combat, damage 10) branched into three hypotheses: digit counter @20[8:11] gives «(:(» — it only sets
# field width; full-width font gives "１０" — readable but wide; and bit 12 @20 gives
# exactly «10». The status panel sets the same value — because the numbers there were always integers.
# The insertion is canonical verbatim: gates.skeleton() trims these two lines FROM BOTH sides,
# otherwise structure-gate would rightfully flag an instruction that doesn't exist in the original.
NUM_ON = '(set-arr~ @ 20 (// (&& (~ @ 20) 4095) 4096))'
NUM_OFF = '(set-arr~ @ 20 (&& (~ @ 20) 4095))'
# ⚠️ Template requires an ADDEND: (+ (&& (~ @ 20) 61695) N) -- this is exactly how the game sets it
# panel digit counter. Once I extended it to any mask 61695 and removed 337 wrappers,
# deciding that they're breaking the game: after removal the crash shifted further. It was a correlation,
# and not the cause -- padding with spaces to the end of the line was breaking the game (§12 STATUS). Extension
# wiped out the working fix, and combat numbers went back to halves again: "dealt :( damage".
_COUNT = re.compile(r'\(set-arr~ @ 20 \(\+ \(&& \(~ @ 20\) 61695\) \d+\)\)')


def number_fix(src):
    """Wrap every (text …) form containing (number …) in "half-width digits enabled".

    ⚠️ Only numbers IN THE SENTENCE. The status panel, item list, and shop price list render
    digits themselves: they set the cursor (@17) and the digit counter (@20 bits 8-11) — do not touch.
    Measured: patching a SINGLE START.MES, nine of thirteen insertions in the panel render, and
    the game is dead on SENTO00 load (emu/battle_probe.py, step 35, reproducible).
    Telltale sign of such a spot — a counter write nearby before the form; plus a bare
    (text (number …)) with not a single letter — that is always a panel, never a phrase.
    """
    spots, last = [], 0
    for m in re.finditer(r'\(text\b', src):
        op = src.index('(', m.start())
        if op < last:
            continue
        els, end = _elements(src, op)
        if not any(e[0] == 'raw' and '(number' in e[1] for e in els):
            continue
        last = end + 1
        if not any(e[0] == 'str' and any(c.isalpha() for c in e[3]) for e in els):
            continue                       # bare numbers — a panel, not a phrase
        if _COUNT.search(src[max(0, op - 400):op]):
            continue                       # a digit counter is placed next to it — also a panel
        # ⚠️ idempotency: recompile.py modifies en/*.rkt IN PLACE, and without this check
        # second pass stacks wrappers one on top of another (verified: (set-arr~ @ 20 …)
        # twice in a row, the skeleton diverges by an extra separator).
        if src[max(0, op - len(NUM_ON) - 4):op].rstrip().endswith(NUM_ON):
            continue
        spots.append((op, end + 1))
    for a, b in reversed(spots):
        src = src[:a] + NUM_ON + ' ' + src[a:b] + ' ' + NUM_OFF + src[b:]
    return src, len(spots)

