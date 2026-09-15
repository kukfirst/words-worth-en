#!/usr/bin/env python3
"""Proofread the translation: pass over the English text once more, now without Japanese in view.

The first pass translated Japanese into English and was judged for fidelity to the original.
Proofreading is a different job: there is no Japanese here at all, and the only question is
"does this read like English?" Literal word order, a dropped article, a dead idiom, inconsistent
forms of address -- exactly what the gbatemp proofreader rightly flagged.

## Where the result is written

NOT to `en/*.rkt`. Edits go back into `text/*.json`, i.e. the same flat form that goes out to
a human, and from there follow the usual path: `tools/import_text.py` with all its gates and the
rule "one failed line means nothing is written." There must not be two roads into the scripts.

So a proofreading pass can be reviewed by eye before it goes anywhere:

    tools/proofread.py                  # run and write sentences into text/
    tools/proofread.py FLOOR02.MES      # one file
    git diff --stat                     # (text/ is gitignored -- view via --review)
    tools/proofread.py --review         # show every accepted edit line by line
    tools/import_text.py                # see what passes the gates
    tools/import_text.py --apply        # write into the scripts

## What gets rejected on the spot, before reaching import_text

| check | why here, not later |
|---|---|
| `{0}` markers | cheaper not to propose than to untangle a 200-line rejection later |
| encoding | same |
| window layout | proofreading tends to lengthen a line; a 56-char window doesn't forgive that |
| length | a line must not take MORE screen lines than it did before: the file grows |
| "edit for its own sake" | a change that doesn't alter meaning (case, a period) only spends budget |

⚠️ The model decides nothing here. It proposes; the code accepts.
"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from render import screen, parts_of, flaws                          # noqa: E402
import gates                                                        # noqa: E402
import llm                                                          # noqa: E402

TEXT = ROOT / 'text'
MARK = re.compile(r'\{(\d+)\}')
# Speaker tag at the start of a line: `[Kaiser]: `, `[{0}]: `. Must never change.
SPEAKER = re.compile(r'^\s*\[[^\]]*\]:\s*')
BATCH = 25

PROMPT = """You are proofreading the English script of a 1993 Japanese dungeon RPG.
The Japanese is gone; judge only whether each line reads as natural English.

Fix: literal word order, wrong or missing articles, dead-literal idioms, wrong register,
clumsy phrasing, inconsistent address between characters.

Do NOT fix: spelling of proper nouns, American vs British spelling, punctuation style,
"..." ellipses, or anything that is merely a matter of taste. Do not censor. Do not soften.
The game is adult and blunt; keep it blunt.

A very common defect in this script: a noun is missing after a group name, because Japanese
does not need one. "took out some Light Clan" must become "took out some Light Clan members".
Adding the missing word is REQUIRED even though it makes the line longer.

HARD RULES:
- {0} and {1} are runtime name insertions. Keep them exactly, same count, same order.
- A line may start with a speaker tag like "[Kaiser]: " or "[{0}]: ". KEEP IT VERBATIM,
  including the brackets, the colon and the space. Never drop it, never add one.
- Do not change leading or trailing spaces.
- Prefer the shortest correct fix, but DO lengthen a line when the fix needs a word. Never
  add so much that the line would need an extra display line.
- Keep the line breaks (\\n) where they are unless the line genuinely needs rewrapping.
- ASCII only.

Input is a numbered list, one reply per numbered item. A reply's own line break inside the
message window is written as the two characters \\n, never as a real newline; keep it that way.

Output ONLY a JSON object mapping the number of each line YOU ACTUALLY CHANGED to its
corrected text. A line that is already fine must NOT appear. Most lines are fine, so most
answers are small; {} is a perfectly good answer. No commentary."""


def wrong(old, new, c0):
    """Reason to reject the proposal. Empty means we take it.

    ⚠️ There is NO "not a single character longer" rule here, and that's a fix. It stood in the
    first version and would have rejected a genuine repair: `took out some Light Clan` ->
    `took out a few Light Clan guys` -- a phrase missing a noun can only be fixed by adding a
    word. The real limit isn't on the line, it's on the FILE: it must not cross `gates.MES_MAX`.
    So here it's the number of screen lines (a window is a window), growth is tracked against a
    per-file budget (`budget`), and the final word still belongs to the size gate in
    `import_text.py`, which now refuses BEFORE writing.
    """
    if new == old:
        return 'unchanged'
    if MARK.findall(old) != MARK.findall(new):
        return f'markers {MARK.findall(old)} -> {MARK.findall(new)}'
    # ⚠️ A marker AT THE START (or the end) of a form isn't decoration, it's the splice point:
    # the engine prints the name BEFORE the form's text, and `{0}` physically cannot move
    # inside the phrase. Measured case: proofreading proposed `{0} to. I'll write.` -> `I'll
    # write in {0}.`, and that's the one edit out of 368 that import rejected ("doesn't fit
    # into the form's slots"). Cheaper not to propose it than to fail the whole import: it's
    # all-or-nothing.
    for end in (True, False):
        o, n = (old.rstrip(), new.rstrip()) if end else (old.lstrip(), new.lstrip())
        om, nm = (MARK.search(o[-4:]), MARK.search(n[-4:])) if end else \
                 (MARK.match(o), MARK.match(n))
        if bool(om) != bool(nm):
            return f'marker at the {"end" if end else "edge"} of the form slid inside the phrase'
    # ⚠️ The speaker tag `[Kaiser]: ` is plain text, not a marker, and the first version didn't
    # guard it. Measured 2026-09-15 on FLOOR05: of 16 accepted edits, FIVE stripped the tag
    # ("[Kaiser]: Hey..." -> "Hey...") and one more added a leading space. That would have
    # shipped into the game and silently removed names from lines.
    o, n = SPEAKER.match(old), SPEAKER.match(new)
    if (o.group(0) if o else None) != (n.group(0) if n else None):
        return f'speaker tag {o.group(0) if o else "none"!r} -> {n.group(0) if n else "none"!r}'
    if (len(old) - len(old.lstrip())) != (len(new) - len(new.lstrip())) or \
            old.rstrip() != old and new.rstrip() == new:
        return 'leading or trailing whitespace changed'
    if gates.gate_charset([new]):
        return 'characters outside game encoding'
    sc_old = screen(parts_of(old), w=None, col0=c0)
    sc_new = screen(parts_of(new), w=None, col0=c0)
    if len(sc_new) > len(sc_old):
        return f'takes {len(sc_new)} lines instead of {len(sc_old)}'
    why = flaws(sc_new, col0=c0)
    if why:
        return 'layout: ' + ', '.join(sorted(set(why)))
    if new.strip().lower() == old.strip().lower():
        return 'edit for its own sake'
    return ''


def budget(name):
    """How many characters the file can gain without approaching the threshold.

    It can't be computed exactly: `(dict-build)` builds the compression dictionary FROM the
    text, and the "character -> byte" relationship isn't linear (editing letters in 40 lines
    changed 27.8% of the file's bytes). So we deliberately lowball it: a third of the byte
    headroom. The real check is the rebuild in `import_text`.
    """
    mes = ROOT / 'en' / f'{name}.rkt.mes'
    if not mes.exists():
        return 0
    return max(0, (gates.MES_MAX - mes.stat().st_size) // 3)


def pass_file(jf, limit):
    rows = json.loads(jf.read_text(encoding='utf-8'))
    left = budget(jf.name[:-5])
    took, refused = 0, {}
    for i in range(0, len(rows), BATCH):
        if limit and took >= limit:
            break
        batch = rows[i:i + BATCH]
        # ⚠️ A line break INSIDE a line is encoded as two characters: otherwise the numbered
        # list gets thrown off by its own line break, and the model answers about the wrong line.
        ask = '\n'.join(f'{n}. {r["en"]}'.replace('\n', '\\n') for n, r in enumerate(batch))
        try:
            out = json.loads(llm.chat(PROMPT, ask))
        except Exception as e:
            refused.setdefault(f'model: {type(e).__name__}', 0)
            refused[f'model: {type(e).__name__}'] += 1
            continue
        if not isinstance(out, dict):
            refused['model returned not an object'] = refused.get('model returned not an object', 0) + 1
            continue
        for k, new in out.items():
            if not isinstance(new, str) or not str(k).strip('.').isdigit():
                continue
            n = int(str(k).strip('.'))
            if not 0 <= n < len(batch):
                refused['number outside batch'] = refused.get('number outside batch', 0) + 1
                continue
            r = batch[n]
            new = new.replace('\\n', '\n')      # back from the two-character line-break encoding
            # start column is recovered from the export: the first screen line
            # is shorter than the text by exactly the indent the engine already printed
            c0 = max(0, len(r['screen'][0]) - len(r['en'].split('\n')[0])) if r['screen'] else 0
            why = wrong(r['en'], new, c0)
            if why:
                refused[why.split(':')[0]] = refused.get(why.split(':')[0], 0) + 1
                continue
            grow = len(new) - len(r['en'])
            if grow > left:
                refused['not enough file budget'] = refused.get('not enough file budget', 0) + 1
                continue
            left -= max(0, grow)
            r['en'] = new
            r['screen'] = screen(parts_of(new), w=None, col0=c0)
            took += 1
    if took:
        jf.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    return took, refused


def main(names, limit):
    if not TEXT.is_dir():
        sys.exit(f'no {TEXT} -- run tools/export_text.py first')
    files = [TEXT / f'{n}.json' for n in names] if names else sorted(TEXT.glob('*.MES.json'))
    total, all_ref = 0, {}
    for jf in files:
        if not jf.exists():
            print(f'  {jf.name}: no such file')
            continue
        took, ref = pass_file(jf, limit)
        total += took
        for k, v in ref.items():
            all_ref[k] = all_ref.get(k, 0) + v
        print(f'  {jf.name[:-5]:16} accepted {took:4d}', flush=True)
    print(f'\naccepted edits: {total}')
    if all_ref:
        print('rejected:')
        for k, v in sorted(all_ref.items(), key=lambda x: -x[1]):
            print(f'   {v:5d}  {k}')
    print('\nnext: tools/import_text.py (preview), then --apply')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--limit', type=int, default=0, help='edits per file, 0 = no limit')
    a = ap.parse_args()
    main(a.names, a.limit)
