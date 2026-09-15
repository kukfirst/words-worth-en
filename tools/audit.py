#!/usr/bin/env python3
"""Run the ENTIRE battery of checks against the finished translation as a whole, not by batch.

## Why this is separate from `translate.py`

During translation, the gates judged EVERY BATCH -- and that's true. But after translation the
text was edited by four more tools: `tighten.py` (cut tails to fit size), `shopfix.py`
(rewrote money lines), `finish_ja.py` (finished off Japanese leftovers), `import_text.py`
(brought proofreading back in). None of them calls the battery as a whole. So the claim
"everything's checked" rests on a state that no longer exists.

Plus the new road -- proofreading -- by definition can't call gates that need the Japanese
original: it never sees it. All the more reason for a pass that does.

Here the Japanese comes from `en/*.MES.orig.rkt`, the English from `en/*.MES.rkt`, and
everything we have runs against them, including what no single road covers:

| check | source |
|---|---|
| charset, emptied, glossary, width, edges | `gates` -- the same ones used during translation |
| speaker tag | here; the Japanese `［name］：` must give the English `[Name]: ` |
| window layout | `render.flaws`, accounting for the start column (`column`) |
| size | `gates.gate_size` |

⚠️ Read-only. Fixes nothing and writes nothing.

    tools/audit.py                # the whole game
    tools/audit.py FLOOR05.MES    # one file
    tools/audit.py --show 20      # more examples per complaint
"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from strings import forms, unescape                                 # noqa: E402
from render import screen, parts_of, flaws                          # noqa: E402
import column                                                       # noqa: E402
import gates                                                        # noqa: E402
import split                                                        # noqa: E402

EN = ROOT / 'en'

# ⚠️ Files that are DELIBERATELY kept in Japanese, and why. Not "noise that got stale", but
# covered cases: name entry screen -- this is a kana grid, the player types the name using Japanese
# characters, and there's nothing to translate. The layout there is defined by cursor coordinates, not by
# line wrapping, therefore the window model is not applicable to it by design.
JAPANESE_BY_DESIGN = {'NAME.MES'}
# Japanese speaker label: ［剣士］： — fullwidth brackets and colon.
JA_SPEAKER = re.compile(r'^\s*[［\[][^］\]]*[］\]]\s*[：:]')
EN_SPEAKER = re.compile(r'^\s*\[[^\]]*\]\s*:')


def family(name):
    """Parent and our satellites: together they carry the ENTIRE text of the source file.

    ⚠️ After the split, you cannot verify form against form by number: some branches moved into the satellite,
    and the parent has fewer forms (FLOOR05: 553 -> 435). This is how the audit lost 39 files out of 84 --
    i.e., half of the game's text, exactly the part with the plot. So count checks run over the family
    as a whole: alignment by number is not needed for them, completeness is.
    """
    out = [name]
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    for c in sorted({c.upper() for c in re.findall(r'mes-call "([^"]+)"', src)}):
        if (EN / f'{c}.rkt').exists() and not (EN / f'{c}.orig.rkt').exists():
            out.append(c)
    return out


_LIT = re.compile(r'"((?:[^"\\]|\\.)*)"')


def literals(path):
    """String literals with content -- a measure of text completeness, stable against re-slicing.

    ⚠️ Counting FORMS for this is not an option, and it has been demonstrated: `itembox.py` rewrote the item menu
    with literals instead of a dictionary, and the form counter showed "START.MES: 27 -> 0" and "each
    SENTO lost 5" -- 28 false alarms out of nowhere, while all the text is intact
    (101 English item names in START.MES, verified by enumeration). Literals, however,
    are visible in any form: 189 vs 189 in START.MES, 421 vs 421 in SENTO02.
    """
    s = pathlib.Path(path).read_text(encoding='utf-8')
    return [x for x in _LIT.findall(s) if x.strip() and x.strip('　 ')]


def labelled(texts):
    """How many lines carry the speaker tag."""
    return sum(1 for t in texts if EN_SPEAKER.match(t) or JA_SPEAKER.match(t))


def speaker_census(name):
    """FAMILY labels vs. the original. Answer: (in Japanese, in English)."""
    ja = [unescape(f['ja']) for f in forms((EN / f'{name}.orig.rkt').read_text(encoding='utf-8'))]
    en = []
    for m in family(name):
        en += [unescape(f['ja']) for f in forms((EN / f'{m}.rkt').read_text(encoding='utf-8'))]
    return labelled(ja), labelled(en), len(ja), len(en)


def unsplit(name):
    """English source in the ORDER OF THE ORIGINAL: a branch is returned in place of `(mes-call …)`.

    ⚠️ Without this, the audit compared 39 files out of 84 by counters alone — and those are all the split
    floors, i.e. half the game text and the entire plot. The split doesn't alter text, it relocates
    branches; hence the operation is reversible, and afterwards the forms realign with the Japanese
    ones one-to-one (verified: FLOOR02 604=604, FLOOR05 553=553, FLOOR08 649=649).
    """
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    comp = {}
    for c in {c.upper() for c in re.findall(r'mes-call "([^"]+)"', src)}:
        p = EN / f'{c}.rkt'
        if not p.exists() or (EN / f'{c}.orig.rkt').exists():
            continue                       # the game's native companion — not our slice
        cs = p.read_text(encoding='utf-8')
        for br in split.branches(cs):
            a, b = split.sexprs(cs, br['span'][0] + 1, br['span'][1] - 1)[0]
            comp[re.sub(r'\s+', ' ', cs[a:b]).strip()] = br['src']
    if not comp:
        return src
    out = src
    for br in sorted(split.branches(src), key=lambda b: -b['span'][0]):
        if 'mes-call' not in br['src']:
            continue
        a, b = split.sexprs(src, br['span'][0] + 1, br['span'][1] - 1)[0]
        cond = re.sub(r'\s+', ' ', src[a:b]).strip()
        if cond in comp:
            s, e = br['span']
            out = out[:s] + comp[cond] + out[e:]
    return out


def unsplit_forms(name):
    """Forms in the ORIGINAL order, but each with ITS OWN file and real offsets.

    ⚠️ `unsplit()` concatenates text and thereby breaks offsets: you must not write by them (this
    already mangled a translation once). Here the order is restored the same way, but the form carries
    the name of the file where it ACTUALLY lives — so a fix can be written in place.

    Returns [(form, filename)].
    """
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    comp = {}
    for c in {c.upper() for c in re.findall(r'mes-call "([^"]+)"', src)}:
        p = EN / f'{c}.rkt'
        if not p.exists() or (EN / f'{c}.orig.rkt').exists():
            continue
        cs = p.read_text(encoding='utf-8')
        for br in split.branches(cs):
            a, b = split.sexprs(cs, br['span'][0] + 1, br['span'][1] - 1)[0]
            cond = re.sub(r'\s+', ' ', cs[a:b]).strip()
            # forms INSIDE the satellite branch, with offsets in the satellite file
            inner = [f for f in forms(cs)
                     if br['span'][0] <= f['start'] < br['span'][1]]
            comp[cond] = (f'{c}.rkt', inner)
    # ⚠️ You cannot navigate by SHAPES: in the branch with `(mes-call …)` there are no shapes left at all — text
    # moved into the satellite, — and such a branch is invisible for form iteration. So we go through
    # EVENTS: parent forms and call branches, sorted by offset.
    events = []
    for f in forms(src):
        events.append((f['start'], 'form', f))
    for br in split.branches(src):
        if 'mes-call' not in br['src']:
            continue
        a, b = split.sexprs(src, br['span'][0] + 1, br['span'][1] - 1)[0]
        cond = re.sub(r'\s+', ' ', src[a:b]).strip()
        if cond in comp:
            events.append((br['span'][0], 'call', comp[cond]))
    out = []
    for _, kind, payload in sorted(events, key=lambda e: e[0]):
        if kind == 'form':
            out.append((payload, f'{name}.rkt'))
        else:
            fname, inner = payload
            out += [(g, fname) for g in inner]
    return out


def pairs(name):
    """(Japanese form, English form, start column) by ordinal number."""
    orig = EN / f'{name}.orig.rkt'
    cur = EN / f'{name}.rkt'
    if not orig.exists() or not cur.exists():
        return None
    src = unsplit(name)
    ja = [unescape(f['ja']) for f in forms(orig.read_text(encoding='utf-8'))]
    fs = forms(src)
    cols = column.columns(src)
    en = [unescape(f['ja']) for f in fs]
    c0 = [cols.get(f['start'], 0) for f in fs]
    if len(ja) != len(en):
        return ('FORM COUNT MISMATCH', len(ja), len(en))
    return ja, en, c0


def check(name, terms):
    ja, en, c0 = pairs(name)
    bad = {}

    def note(kind, what):
        bad.setdefault(kind, []).append(what)

    if name in JAPANESE_BY_DESIGN:
        return bad                      # see JAPANESE_BY_DESIGN
    for g, res in (('encoding', gates.gate_charset(en)),
                   ('emptied', gates.gate_emptied(ja, en)),
                   ('glossary', gates.gate_glossary(ja, en, terms)),
                   ('width', gates.gate_width(ja, en, 56))):
        if res:
            note(g, res if isinstance(res, str) else str(res))

    # ⚠️ `gate_edges` -- a report, not a gate, and it says so itself: the menu item legitimately starts
    # with `は` («はい» -> «Yes»). Measurement 2026-09-15: all 4 of his objections turned out to be items
    # MENU where Japanese starts with は/も not as a particle, but as the first syllable of a word
    # («はっきり» -- «honestly», «もちろん» -- «of course»). We crop the menu by its borders,
    # which the game itself provides (`gates.menu_spans`), rather than a word list.
    try:
        spans = gates.menu_spans((EN / f'{name}.rkt').read_text(encoding='utf-8'))
    except Exception:
        spans = []
    in_menu = set()
    if spans:
        fs = forms((EN / f'{name}.rkt').read_text(encoding='utf-8'))
        for i, f in enumerate(fs):
            if any(s <= f['start'] < e for s, e in spans):
                in_menu.add(i)
    for row in gates.gate_edges(ja, en)[:99]:
        if row[0] in in_menu:
            continue
        note('name seam', f'#{row[0]} {row[-1]!r}')

    for i, (a, b, c) in enumerate(zip(ja, en, c0)):
        if not a.strip() or not b.strip():
            continue
        # ⚠️ Speaker's caption. There was no gate for it ON NO SINGLE road: it was not the old one
        # needed (the model wrapped the line together with the caption), but the new one doesn't detect Japanese.
        # Discovered 2026-09-15, when the proofread stripped `[Kaiser]: ` from five consecutive lines.
        if bool(JA_SPEAKER.match(a)) != bool(EN_SPEAKER.match(b)):
            note('speaker tag', f'#{i} ja={a[:24]!r} en={b[:34]!r}')
        # ⚠️ Proper name starting with a LOWERCASE letter. The Katakana in the Japanese subtitle is a name
        # (［クラブ］), and the English one must start with a capital letter. Caught by player:
        # `［クラブ］` gave `[Club]` 69 times and `[club]` 29 times, and the word "club" -- and also
        # clunky, so it doesn't stand out visually and the glossary was silent (it's not in there).
        # ⚠️ Line break INSIDE the label splits the speaker's name in half (`[Armor Shop\nOwner]:`),
        # but a leading line break produces an empty line before the reply. It appears not during translation,
        # and on LAYOUT SWITCH, so no translation gate sees this.
        # Found 2026-09-15 by the `consistency` step, when it finally made it into the pipeline.
        # ⚠️ Look ONLY between the brackets: `EN_SPEAKER` captures the leading newline as well,
        # and the check was flagging its own healing as a defect (caught immediately).
        head = re.match(r'\s*\[([^\]]*)\]', b)
        if head and '\n' in head.group(1):
            note('line break inside tag', f'#{i} {b[:34]!r}')
        # ⚠️ Leading newline before the signature -- NOT a bug, but a fix: when the form is printed
        # from column 39, the speaker's name doesn't fit in the remainder of the line, so it needs to start from
        # new. At first I considered it a defect and removed it -- the layout immediately turned red on the same
        # five replicas. Remove the line break INSIDE the signature, replacing it with a leading one.
        ja_tag, en_tag = JA_SPEAKER.match(a), EN_SPEAKER.match(b)
        if ja_tag and en_tag:
            inner_ja = ja_tag.group(0).strip('［[］]：: ')
            inner_en = en_tag.group(0).strip('[]: ')
            if (re.fullmatch(r'[ァ-ヶー・]+', inner_ja) and inner_en
                    and inner_en[0].isalpha() and inner_en[0].islower()):
                note('lowercase name', f'#{i} ja=［{inner_ja}］ en=[{inner_en}]')
        # ⚠️ A form starting with name substitution -- known exception: line break
        # put NOWHERE, first line is entirely occupied by someone else's name. `verify.py` considers it so
        # them on a separate line («plus N forms starting with the substitution») precisely for this reason.
        if b.lstrip().startswith('{'):
            continue
        why = flaws(screen(parts_of(b), w=None, col0=c), col0=c)
        if why:
            note('layout', f'#{i} {sorted(set(why))} {b[:34]!r}')

    mes = EN / f'{name}.rkt.mes'
    if mes.exists() and gates.gate_size(mes):
        note('size', f'{mes.stat().st_size} b past threshold {gates.MES_MAX}')
    return bad


def main(names, show):
    terms = json.loads((ROOT / 'glossary.json').read_text(encoding='utf-8'))
    totals, examples = {}, {}
    skipped = []
    for name in names:
        p = pairs(name)
        if p is None:
            skipped.append(name)
            continue
        # Split file: can't cross-check by numbers, but the family can be recounted in its entirety.
        if isinstance(p, tuple) and p and p[0] == 'FORM COUNT MISMATCH':
            sj, se, nj, ne = speaker_census(name)
            # Completeness -- by LITERALS, not by forms (see literals(): forms give 28 false positives).
            lj = len(literals(EN / f'{name}.orig.rkt'))
            le = sum(len(literals(EN / f'{m}.rkt')) for m in family(name))
            # ⚠️ Growth is normal and not a cause for alarm: the slice gives the satellite its own preamble with
            # COMPRESSION DICTIONARY (`dict-build`), and these are literals, plus the filename in `mes-call`.
            # Measurement: FLOOR08 1084 -> 1117. Alarm -- only on DECREASE.
            if le < lj:
                totals['text lost'] = totals.get('text lost', 0) + (lj - le)
                examples.setdefault('text lost', []).append(
                    f'{name}: literals {lj} -> {le} across family {"+".join(family(name))}')
            if sj != se:
                totals['tags lost'] = totals.get('tags lost', 0) + abs(sj - se)
                examples.setdefault('tags lost', []).append(
                    f'{name}: tags {sj} -> {se}')
            continue
        for kind, items in check(name, terms).items():
            totals[kind] = totals.get(kind, 0) + len(items)
            examples.setdefault(kind, []).extend(f'{name} {x}' for x in items)

    print(f'files checked: {len(names) - len(skipped)}'
          f'{f", without a Japanese original (our satellites): {len(skipped)}" if skipped else ""}')
    if not totals:
        print('\n✅ no complaints')
        return 0
    print()
    for kind, n in sorted(totals.items(), key=lambda x: -x[1]):
        print(f'❌ {kind}: {n}')
        for x in examples[kind][:show]:
            print(f'     {x}')
    print(f'\ntotal complaints: {sum(totals.values())}')
    return 1


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--show', type=int, default=6)
    a = ap.parse_args()
    ns = a.names or sorted(p.name[:-4] for p in EN.glob('*.MES.rkt')
                           if not p.name.endswith('.orig.rkt'))
    sys.exit(main(ns, a.show))
