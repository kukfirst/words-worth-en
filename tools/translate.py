#!/usr/bin/env python3
"""Words Worth (PC-98, elf/AI5) JA->EN translation pipeline.

Deterministic conveyor: the model only ever translates or reviews; every
accept/reject decision is made by code. State lives in git + progress.jsonl,
so a kill at any moment costs at most one batch.
"""
import argparse, json, pathlib, re, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from llm import chat, TooSlow
from strings import forms, patch, rebuild_dict, split_translation, number_fix
import gates

ROOT = pathlib.Path(__file__).resolve().parent.parent
BATCH_DEADLINE = 150  # seconds before a batch is abandoned and split
WORK = ROOT / 'work'
OUT = ROOT / 'en'
PROGRESS = ROOT / 'progress.jsonl'
SUMMARY = ROOT / 'story.md'

SYS_TR = """You translate a 1993 Japanese fantasy RPG (Words Worth, elf) into English.

RULES
- Output STRICT JSON: an array of strings, EXACTLY as many as the input array, same order.
- Translate each string independently but consistently; they are consecutive lines of one scene.
- ASCII only. No smart quotes, no em dashes, no accented letters. Use ' and " and -.
- Keep it terse: this is a 1993 game with a small text box. Prefer short natural phrasing.
- Preserve the register: menu commands stay imperative ("Equip a weapon"), dialogue stays spoken.
- An empty input string stays an empty output string.
- Never merge or split lines. Never add commentary.
- Adult content is present; translate it plainly and accurately, do not censor or soften.
- Use the glossary EXACTLY for every term listed.
- Keep the punctuation that surrounds a marker. ［{0}］： is a speaker label and must
  come back as [{0}]: -- never drop the brackets, or the line breaks the game.
- {0}, {1} ... are runtime insertions of the hero's name. Keep every marker, exactly once,
  in the same order, spelled exactly as {0}. Never translate or drop a marker.
- Japanese honorifics attached to a marker (さん, 様, ちゃん) are dropped in English unless
  the line is mocking or ceremonial: ［A］：{0}様、… becomes [A]: {0}, … not "{0}-sama".
- A line that OPENS with a bound particle (は, が, を, に, と, も, で) or with 、 is not a
  sentence: the engine has just printed a name or a number hard against it. Continue it --
  keep the leading space ("は、痛そうな顔をした！！" -> " winced in pain!!", never
  "You winced in pain!!") -- or open with 's for の."""

SYS_REV = """You are reviewing an English translation of a 1993 Japanese RPG against its source.

Report ONLY real problems, as STRICT JSON: an array of {"i": <index>, "why": "<short reason>"}.
Look for: wrong meaning, lost information, invented content, broken register (a menu command
written as dialogue or vice versa), a name spelled against the glossary, or text so long it
clearly will not fit a small box. Ignore matters of taste. If everything is acceptable,
output []."""

SYS_SUM = ("Summarise what happens in this scene of a fantasy RPG in at most 4 short English "
           "sentences, for use as context when translating the next scene. Plain text only.")


def load_glossary():
    g = json.load(open(ROOT / 'glossary.json'))
    terms = g['terms']
    # only proper nouns are a hard gate; common nouns are hints, not law
    locked = {k: v for k, v in terms.items() if v[:1].isupper()}
    return terms, locked


def json_array(raw, n=None):
    raw = re.sub(r'^```(?:json)?|```$', '', raw.strip(), flags=re.M).strip()
    m = re.search(r'\[.*\]', raw, re.S)
    if m:
        raw = m.group(0)
    v = json.loads(raw)
    if n is not None and not isinstance(v, list):
        raise ValueError('not a list')
    return v


def translate_batch(ja, glossary, locked, story, tail, width, attempts=3):
    used = {k: v for k, v in glossary.items() if any(k in s for s in ja)}
    ctx = []
    if story:
        recent = story.split('\n## ')
        ctx.append('STORY SO FAR:\n' + ('## ' + '\n## '.join(recent[-3:]) if len(recent) > 1 else story))
    if tail:
        ctx.append('PREVIOUS LINES (already translated, for continuity):\n' +
                   '\n'.join(f'{a}  ->  {b}' for a, b in tail))
    if used:
        ctx.append('GLOSSARY (use exactly):\n' +
                   '\n'.join(f'{k} = {v}' for k, v in used.items()))
    ctx.append(f'LENGTH: the engine wraps at {width} characters, so a long line is fine, '
               'but keep the translation about as compact as the Japanese original.')
    ctx.append('TRANSLATE THIS ARRAY:\n' + json.dumps(ja, ensure_ascii=False))
    prompt = '\n\n'.join(ctx)
    last = None
    stats = {'attempts': 0, 'prompt_chars': len(prompt), 'why': []}
    for a in range(attempts):
        stats['attempts'] = a + 1
        t0 = time.time()

        def on_delta(text, ntok, _a=a):
            if ntok % 8:
                return
            live(phase='translate', attempt=_a + 1, gen_tokens=ntok,
                 gen_secs=round(time.time() - t0, 1),
                 tok_s=round(ntok / max(time.time() - t0, .001), 1),
                 gen_tail=text[-400:], lines_done=text.count('",'))

        try:
            raw = chat(SYS_TR, prompt if a == 0 else prompt + f'\n\nPREVIOUS ATTEMPT REJECTED: {last}',
                       max_tokens=16000, on_delta=on_delta, deadline=BATCH_DEADLINE)
        except TooSlow as e:
            # Shop files (SHP_*) reliably send the model into 5-8 minute spirals.
            # Halving is cheaper than waiting it out: the halves come back in seconds.
            last = str(e)
            break
        try:
            en = json_array(raw, len(ja))
            en = ['' if x is None else str(x) for x in en]
            # the model writes literal \n (and sometimes real newlines/tabs) to fake
            # line breaks; the engine word-wraps by itself, and a backslash is not in
            # charset "english" -- it makes the compiled file unparseable.
            en = [re.sub(r'\\+[nrt]|[\r\n\t]+', ' ', x) for x in en]
            # ⚠️ rstrip, not strip: a line that continues a name the engine just
            # printed HAS to open with a space (" winced in pain!!"). .strip()
            # ate it, which is half of why 345 lines shipped as «AstralYou ...».
            en = [re.sub(r'  +', ' ', x).rstrip() if x.strip() else x for x in en]
        except Exception as e:
            last = f'output was not a JSON array ({e})'
            continue
        marker_bad = None
        for idx, (src_line, out_line) in enumerate(zip(ja, en)):
            want = re.findall(r'\{(\d+)\}', src_line)
            got = re.findall(r'\{(\d+)\}', out_line)
            if want != got:
                marker_bad = f'line {idx}: markers {want} became {got}'
                break
        for g in (gates.gate_count(ja, en), marker_bad,
                  gates.gate_emptied(ja, en),
                  gates.gate_charset(en),
                  gates.gate_glossary(ja, en, {k: v for k, v in locked.items()
                                               if any(k in s for s in ja)}),
                  gates.gate_width(ja, en, width)):
            if g:
                last = g
                break
        else:
            return en, None, stats
        if last:
            stats['why'].append(str(last)[:120])
    return None, last, stats


def translate_split(ja, glossary, locked, story, tail, width, report, depth=0):
    """Translate a chunk; on failure halve it and retry the halves.

    Measured: the model returns the right number of lines up to ~40 per call and
    starts dropping them past that (41 of 60). Halving turns a total loss into a
    partial one -- only the pieces that genuinely fail keep their Japanese.
    """
    en, why, stats = translate_batch(ja, glossary, locked, story, tail, width)
    report(event='batch_stats', **stats)
    if en is not None:
        return en, None
    if len(ja) <= 4 or depth >= 4:
        report(event='batch_reject', n=len(ja), why=str(why)[:200])
        return list(ja), why
    report(event='batch_split', n=len(ja), why=str(why)[:120])
    mid = len(ja) // 2
    a, _ = translate_split(ja[:mid], glossary, locked, story, tail, width, report, depth + 1)
    b, _ = translate_split(ja[mid:], glossary, locked, story,
                           list(zip(ja[:mid], a))[-4:], width, report, depth + 1)
    return a + b, None


def review(pairs, glossary, width):
    body = json.dumps([{'i': i, 'ja': a, 'en': b} for i, (a, b) in enumerate(pairs)],
                      ensure_ascii=False)
    used = {k: v for k, v in glossary.items() if any(k in a for a, _ in pairs)}
    prompt = (('GLOSSARY:\n' + '\n'.join(f'{k} = {v}' for k, v in used.items()) + '\n\n' if used else '')
              + f'BOX WIDTH: {width}\n\nPAIRS:\n' + body)
    try:
        return json_array(chat(SYS_REV, prompt, max_tokens=4000))
    except Exception:
        return []


def summarise(pairs):
    en = '\n'.join(b for _, b in pairs if b.strip())[:6000]
    if not en.strip():
        return ''
    try:
        return chat(SYS_SUM, en, max_tokens=600).strip()
    except Exception:
        return ''


LIVE = ROOT / 'live.json'
_live = {}

def live(**kw):
    """Heartbeat the dashboard reads. Written often and cheaply."""
    _live.update(kw)
    _live['ts'] = time.time()
    tmp = LIVE.with_suffix('.tmp')
    tmp.write_text(json.dumps(_live, ensure_ascii=False))
    tmp.replace(LIVE)


def log(rec):
    with open(PROGRESS, 'a') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    print(f"  {rec.get('event')}: " +
          ' '.join(f'{k}={v}' for k, v in rec.items() if k not in ('event', 'ts')), flush=True)


def done_files():
    if not PROGRESS.exists():
        return set()
    return {json.loads(l)['file'] for l in open(PROGRESS)
            if l.strip() and json.loads(l).get('event') == 'file_done'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch', type=int, default=40)
    ap.add_argument('--width', type=int, default=46)
    ap.add_argument('--review-every', type=int, default=4, help='review after N batches')
    ap.add_argument('--limit-files', type=int, default=0)
    ap.add_argument('--only', default='')
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    glossary, locked = load_glossary()
    units = json.load(open(ROOT / 'units.json'))
    by_file = {}
    for u in units:
        by_file.setdefault(u['file'], []).append(u)

    already = done_files()
    order = sorted(by_file, key=lambda f: len(by_file[f]))
    if args.only:
        order = [f for f in order if args.only in f]
    order = [f for f in order if f not in already]
    if args.limit_files:
        order = order[:args.limit_files]
    print(f'{len(order)} files to do ({len(already)} already done)', flush=True)

    story = SUMMARY.read_text() if SUMMARY.exists() else ''

    for fname in order:
        us = by_file[fname]
        name = fname[:-4]                     # FLOOR01.MES.rkt -> FLOOR01.MES
        src = (WORK / fname).read_text(encoding='utf-8')
        t0 = time.time()
        log({'event': 'file_start', 'file': fname, 'units': len(us), 'ts': time.time()})

        edits, pairs, tail, failed = [], [], [], 0
        # layout-only lines (ideographic spaces, punctuation runs) are not text:
        # translating them wastes model time and, when the model returns "",
        # juice fails to compile the file at all.
        def is_layout(t):
            # only whitespace runs -- ideographic spaces used for layout. Punctuation
            # like ！！ still goes to the model, which turns it into !!.
            return bool(t) and not t.strip('\u3000 \t\u00a0')

        for bi in range(0, len(us), args.batch):
            chunk = us[bi:bi+args.batch]
            ja = [c['ja'] for c in chunk]
            keep = [i for i, t in enumerate(ja) if not is_layout(t)]
            tb = time.time()
            live(phase='translate', file=fname, batch=bi // args.batch + 1,
                 batches=(len(us) + args.batch - 1) // args.batch,
                 batch_ja=ja, attempt=1, gen_tokens=0, gen_tail='', lines_done=0,
                 started=tb, file_units=len(us), file_at=bi)
            if keep:
                sub, why = translate_split([ja[i] for i in keep], glossary, locked, story,
                                           tail[-4:], args.width,
                                           lambda **kw: log(dict(kw, file=fname, at=bi)))
            else:
                sub, why = [], None
            en = list(ja)                       # layout lines pass through untouched
            for pos, val in zip(keep, sub):
                en[pos] = val
            miss = sum(1 for a, b in zip(ja, en) if a == b and a.strip())
            failed += miss
            for c, e in zip(chunk, en):
                pieces = split_translation(e, len(c['slots']), len(c['ins']),
                                           c.get('lead', 0), c.get('trail', 0))
                if pieces is not None:
                    # a slot that carried text must not come back empty: juice dies
                    # compiling an empty text slot ('max: arity mismatch'). Happens
                    # when the model drops the ［］ around a speaker marker.
                    src_slots = [src[a:b] for a, b in c['slots']]
                    if any(o.strip() and not n.strip() for o, n in zip(src_slots, pieces)):
                        log({'event': 'slot_emptied', 'file': fname, 'ja': c['ja'][:60],
                             'en': e[:60]})
                        pieces = None
                if pieces is None:
                    # keep the Japanese for this form rather than inventing empty
                    # slots -- an empty text slot makes the file uncompilable.
                    pieces = [src[a:b] for a, b in c['slots']]
                    log({'event': 'split_fallback', 'file': fname, 'ja': c['ja'][:80]})
                for (a, b), piece in zip(c['slots'], pieces):
                    edits.append((a, b, piece.replace('\\', '\\\\').replace('"', '\\"')))
            log({'event': 'batch', 'file': fname, 'at': bi, 'n': len(ja),
                 'secs': round(time.time() - tb, 1),
                 'story_chars': len(story), 'tail_n': len(tail[-4:]),
                 'pairs': [[a, b] for a, b in zip(ja, en)]})
            pairs += list(zip(ja, en))
            tail = list(zip(ja, en))
            nb = bi // args.batch + 1
            if args.review_every and nb % args.review_every == 0:
                live(phase='review', file=fname, batch=nb)
                probs = review(pairs[-args.batch*args.review_every:], glossary, args.width)
                if probs:
                    log({'event': 'review', 'file': fname, 'at': bi, 'issues': len(probs),
                         'sample': str(probs[:3])[:300]})

        out_src = patch(src, edits)
        out_src = re.sub(r'\(charset "pc98"\)', '(charset "english")', out_src)
        # ⚠️ (wordwrap …) в meta НЕ ставим. Раньше ставили -- и компилятор резал каждый
        # строковый кусок сам, от нулевой колонки, не зная про подстановку имени; вместе с
        # переносами движка это давало слова-сироты и дыры (STATUS.md §16). Раскладку теперь
        # решает tools/relayout.py и ставит разрывы явно. Вернуть сюда wordwrap -- значит
        # тихо сломать её на следующем же прогоне конвейера.
        out_src = re.sub(r'\(set-arr~ @ 21 \(\+ 512 16\)\)', '(set-arr~ @ 21 272)', out_src)
        out_src, _ = number_fix(out_src)
        out_src = rebuild_dict(out_src)
        (OUT / f'{name}.rkt').write_text(out_src, encoding='utf-8')
        (OUT / f'{name}.orig.rkt').write_text(src, encoding='utf-8')

        live(phase='gates', file=fname, batch=None, batch_ja=[], gen_tail='')
        g = gates.gate_compile_and_structure(OUT, name)
        log({'event': 'file_done', 'file': fname, 'units': len(us), 'failed': failed,
             'gate': g or 'ok', 'secs': round(time.time() - t0, 1)})

        live(phase='summarise', file=fname)
        s = summarise(pairs)
        if s:
            story = (story + f'\n\n## {name}\n{s}').strip()
            SUMMARY.write_text(story)

main()
