#!/usr/bin/env python3
"""Translate Russian comments and docstrings into English -- without touching the code.

The public repository goes out to people who don't read Russian, and the whole point of our
tools lives in the comments: why the threshold is what it is, why a list is computed rather
than written by hand, what burned us last time. Without translation, the code ships with no
explanations.

## Why this isn't "run the file through the model"

A model rewriting a whole .py file will, sooner or later, swap an argument or eat a negation,
and it will go unnoticed: diffs here run hundreds of lines. So only the values of COMMENT
tokens and docstring string literals **change**, and the proof is a re-tokenization:

| check | what it catches |
|---|---|
| token stream | a changed token type or count -- i.e. touched code |
| positions | an edit to a token that wasn't selected |
| compilation | syntax broken by a quote or bracket inside a comment |
| Cyrillic | an untranslated chunk |
| markers | an eaten `⚠️`, link, filename, or number -- the model loves to drop these |

⚠️ Writes nothing without `--apply`. A file where even one check fails to match is not written
AT ALL -- half a translated file is worse than an untranslated one.

    tools/comments_en.py                 # what's found and what will change
    tools/comments_en.py --apply         # write
    tools/comments_en.py tools/gates.py  # one file
"""
import argparse
import io
import pathlib
import re
import sys
import tokenize

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import llm                                                          # noqa: E402

CYR = re.compile(r'[А-Яа-яЁё]')
# Chunks that must survive translation verbatim: without them a comment loses its address.
KEEP = re.compile(r'⚠️|§\d+|`[^`]+`|\b\d[\d_ ]*\b|[A-Za-z_][A-Za-z0-9_]*\.(?:py|rkt|mes|json|md)\b')

PROMPT = """You translate Russian source-code comments into English.

Rules:
- Translate the MEANING, in the voice of an engineer writing for other engineers.
- Keep every backtick span, every number, every filename, every § reference and every ⚠️
  EXACTLY as-is. They are addresses, not prose.
- Keep the leading '#' and the original indentation of each line.
- Keep the line structure: N lines in, N lines out.
- Do not add anything. Do not explain. Do not soften warnings.
- Output ONLY the translated block."""


def chunks(src):
    """Token spans we're allowed to change: comments and docstring strings."""
    out = []
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    for i, t in enumerate(toks):
        if t.type == tokenize.COMMENT:
            out.append(t)
        elif t.type == tokenize.STRING:
            # docstring -- a string standing alone, as a whole statement
            prev = next((p for p in reversed(toks[:i])
                         if p.type not in (tokenize.NL, tokenize.NEWLINE,
                                           tokenize.INDENT, tokenize.DEDENT)), None)
            # A module docstring follows the shebang COMMENT, not a NEWLINE: leaving
            # COMMENT out of this list meant every file's main description was skipped
            # while its inline comments were translated. Caught by reading emu/visit.py
            # after a successful run -- the check was the file itself, not the log.
            if prev is None or prev.type in (tokenize.NEWLINE, tokenize.INDENT,
                                             tokenize.DEDENT, tokenize.COMMENT) \
                    or prev.string == ':':
                out.append(t)
    return [t for t in out if CYR.search(t.string)], toks


def skeleton(toks):
    """File skeleton: the types of every token plus the values of the ones we must not change.

    ⚠️ Positions are deliberately NOT included here. The first version compared `start`/`end`
    and rejected every translation: English text has a different length, and columns shift for
    everything to the right. That caught its own arithmetic, not corrupted code.
    """
    return [(t.type, None) if t.type in (tokenize.COMMENT, tokenize.STRING)
            else (t.type, t.string) for t in toks]


def replace(src, edits):
    """Substitute new values by span. Work from the end so coordinates don't shift."""
    lines = src.splitlines(keepends=True)
    for tok, new in sorted(edits, key=lambda e: e[0].start, reverse=True):
        (r1, c1), (r2, c2) = tok.start, tok.end
        head = lines[r1 - 1][:c1]
        tail = lines[r2 - 1][c2:]
        lines[r1 - 1:r2] = [head + new + tail]
    return ''.join(lines)


def kept(before, after):
    """What was lost from the must-keep set."""
    was = sorted(KEEP.findall(before))
    now = sorted(KEEP.findall(after))
    return [x for x in was if was.count(x) > now.count(x)]


def translate(tok):
    """One token -> English. Returns None if the model failed."""
    body = tok.string
    out = llm.chat(PROMPT, body)
    if not out:
        return None
    out = out.strip('\n')
    if body.startswith('#') and not out.lstrip().startswith('#'):
        return None
    if body.lstrip()[:3] in ('"""', "'''"):
        q = body.lstrip()[:3]
        if not (out.lstrip().startswith(q) and out.rstrip().endswith(q)):
            return None
    if len(body.splitlines()) != len(out.splitlines()):
        return None
    if CYR.search(out):
        return None
    return out


def do(path, apply):
    src = path.read_text(encoding='utf-8')
    try:
        todo, toks = chunks(src)
    except tokenize.TokenError as e:
        return f'{path}: does not parse: {e}', 0
    if not todo:
        return None, 0

    edits, bad = [], []
    for tok in todo:
        new = translate(tok)
        if new is None:
            bad.append(f'line {tok.start[0]}: model failed')
            continue
        lost = kept(tok.string, new)
        if lost:
            bad.append(f'line {tok.start[0]}: lost verbatim {lost[:4]}')
            continue
        edits.append((tok, new))

    if bad:
        return f'{path.name}: ' + '; '.join(bad[:3]), len(todo)

    out = replace(src, edits)
    # ⚠️ Main proof: the skeleton must match. If the model touched the code --
    # the token type or count will change, and it shows here, not in a 300-line review.
    try:
        new_toks = list(tokenize.generate_tokens(io.StringIO(out).readline))
    except tokenize.TokenError as e:
        return f'{path.name}: does not parse after the edit: {e}', len(todo)
    if skeleton(toks) != skeleton(new_toks):
        return f'{path.name}: SKELETON MISMATCH -- code was touched, file not written', len(todo)
    try:
        compile(out, str(path), 'exec')
    except SyntaxError as e:
        return f'{path.name}: does not compile: {e}', len(todo)

    if apply:
        path.write_text(out, encoding='utf-8')
    return None, len(todo)


def main(paths, apply):
    total, failed = 0, []
    for p in paths:
        err, n = do(p, apply)
        if n:
            mark = '❌' if err else ('written' if apply else 'ready')
            print(f'  {p.relative_to(ROOT)!s:32} blocks {n:4d}  {mark}', flush=True)
        if err:
            print(f'      {err}', flush=True)
            failed.append(p)
        total += n
    print(f'\ntotal blocks: {total}, files rejected: {len(failed)}')
    if not apply:
        print('NOT WRITTEN. Apply: tools/comments_en.py --apply')
    return 1 if failed else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='*')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    ps = [pathlib.Path(x).resolve() for x in a.paths] or sorted(
        p for d in ('tools', 'emu') for p in (ROOT / d).glob('*.py'))
    sys.exit(main(ps, a.apply))
