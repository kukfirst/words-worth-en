#!/usr/bin/env python3
"""Fix the item name field — in the field and in all battle scenes.

    python3 tools/itembox.py [--check]

The same item list lives in 28 files: `START.MES`, `START1.MES`, and 26 battle
`SENTO*` files. In each one it is drawn via `(set-arr~ @ 17 2 232)` and suffers from two diseases.

**Disease 1 — dictionary.** `(text …)` compiles through the `.MES` dictionary (128 common
characters, single-byte indices), but this field does NOT decompress the dictionary: index bytes
get paired up and fed into the kanji glyph generator. On screen — half kanji.
`(str …)` writes a literal and bypasses the dictionary. The Japanese original survived by accident:
its dictionary has four characters, none of which appeared in item names. Details: STATUS.md §13.

**Disease 2 — inconsistency.** Files were translated independently, and item 902 is named seven
different ways (`Gold Bar`, `Gold Chunk`, `Gold Ingot`, `Gold Lump`, `Gold Nugget`,
`Lump of Gold`, `Gold Ingots`). Here the name is chosen by case, not by what
the translator wrote.

⚠️ Field width is 14 narrow character cells (`x=16..128`, beyond that is the panel frame). Measured on
the UNTOUCHED Japanese image: there `消炎草　08` is drawn right on the panel, and the original's own
padding (7 full-width spaces) is what sets those 14. The two-digit counter is pinned to cells 13–14,
so the name plus counter caps at 12.

⚠️ Full names remain in the shop and in dialogue — there is plenty of width there and seeing the item
in full is more convenient. Abbreviations live only here. This is a decision, not an oversight.

⚠️ A run of spaces in a single literal already killed the game (measured: 50 crashes, 12 survives, ceiling 16),
so the padding is two literals of 7, not one of 14.

The pass is idempotent: `recompile.py` rewrites `en/*.rkt` in place, and a second run
must not change anything.
"""
import pathlib, re, sys

EN = pathlib.Path(__file__).resolve().parent.parent / 'en'
FIELD = 14                      # character limit in field
COUNT = 2                       # counter digits

NUM_ON  = '(set-arr~ @ 20 (// (&& (~ @ 20) 4095) 4096))'
NUM_OFF = '(set-arr~ @ 20 (&& (~ @ 20) 4095))'
BLANK_JA = '(text "　　　　　　　")'
BLANK_EN = '(str "       ") (str "       ")'          # 14 character positions with two literals

# Field label by item register. Abbreviations are derived from canonical full names,
# that remain in the shop and replicas: Healing Herb / Stamina Herb / Gold Ingot /
# Ascension Stone.
# Canonical names of consumable items. Files were translated independently, and one and
# the same item received up to FIVE different names: 902 was called Gold Lump / Gold Ingot /
# Gold Nugget / Lump of Gold / Gold Chunk. Here the name is singular — and the SHORTEST of them was chosen
# considered variants: then the edit only shortens files, and growth — the only thing by which
# you can accidentally hit the script buffer limit (40 000 B) or the window width.
CANON = {900: 'Healing Herb', 901: 'Stamina Herb', 902: 'Gold Bar', 903: 'Ascension Stone'}

# variant -> canonical. Plural goes first: otherwise "Gold Nuggets" breaks after replacement
# the singular will become «Gold Bars» via «Gold Bar»+«s» only by lucky
# coincidences, and «Lumps of Gold» won't match at all.
VARIANTS = [
    # ⚠️ Notations that canon.py introduces: it resolves by TRUNCATION, not by subject, and
    # For 消炎草/絶倫草 I chose the translations independently in different places -- hence Soothe/Vigor/Vigor Grass
    # next to the already known Soothing/Virile. Long phrases come first: substitution is done by
    # to the list in order, otherwise «Vigor Grass» would have eaten the tail of «Endless Vigor Grass».
    ('Endless Vigor Grass', 'Stamina Herb'), ('Vigor Grass', 'Stamina Herb'),
    ('Soothe Herbs', 'Healing Herbs'), ('Soothe Herb', 'Healing Herb'),
    ('Vigor Herbs', 'Stamina Herbs'), ('Vigor Herb', 'Stamina Herb'),
    ('Soothing Herbs', 'Healing Herbs'), ('Soothing Herb', 'Healing Herb'),
    ('Virile Herbs', 'Stamina Herbs'), ('Virile Herb', 'Stamina Herb'),
    ('Vitality Herbs', 'Stamina Herbs'), ('Vitality Herb', 'Stamina Herb'),
    ('Virility Herbs', 'Stamina Herbs'), ('Virility Herb', 'Stamina Herb'),
    ('Lumps of Gold', 'Gold Bars'), ('Lump of Gold', 'Gold Bar'),
    ('Gold Ingots', 'Gold Bars'), ('Gold Ingot', 'Gold Bar'),
    ('Gold Nuggets', 'Gold Bars'), ('Gold Nugget', 'Gold Bar'),
    ('Gold Lumps', 'Gold Bars'), ('Gold Lump', 'Gold Bar'),
    ('Gold Chunks', 'Gold Bars'), ('Gold Chunk', 'Gold Bar'),
]

# Field label: abbreviated form of the canonical name, because there are only 14 character positions here.
LABEL = {900: 'Heal Herb', 901: 'Stam. Herb', 902: 'Gold Bar', 903: 'Asc. Stone'}

_COUNTED = re.compile(r'\(text "([^"]*)" \(number \(: (\d+)\)\)\)')
# already-converted label -- so the run is idempotent AND picks up a rename
_RELABEL = re.compile(r'\(str "([^"]*)"\) \(text \(number \(: (\d+)\)\)\)')
_PLAIN = re.compile(r'\(text "([^"]+)"\)')
_PAD = re.compile(r'^\s*\(str " +"\)\s*$')


def region(src):
    """Boundaries of a cond with a subject list: from branch 0 to the end of the enclosing cond."""
    m = re.search(r'\(\(== \(~ @ 23\) 0\)', src)
    if not m:
        return None
    start = src.rfind('(cond', 0, m.start())
    depth, i = 0, start
    while i < len(src):
        if src[i] == '(':
            depth += 1
        elif src[i] == ')':
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    return None


def fix(src):
    r = region(src)
    if not r:
        return src, 0
    a, b = r
    seg, n = src[a:b], 0

    def counted(m):
        nonlocal n
        n += 1
        reg = int(m.group(2))
        name = LABEL.get(reg, m.group(1))[:FIELD - COUNT].ljust(FIELD - COUNT)
        return f'{NUM_ON} (str "{name}") (text (number (: {reg}))) {NUM_OFF}'

    def plain(m):
        nonlocal n
        n += 1
        return '(str "%s")' % m.group(1)[:FIELD]

    def relabel(m):
        nonlocal n
        reg = int(m.group(2))
        want = LABEL.get(reg, m.group(1).rstrip())[:FIELD - COUNT].ljust(FIELD - COUNT)
        if m.group(1) != want:
            n += 1
        return f'(str "{want}") (text (number (: {reg})))'

    seg = _COUNTED.sub(counted, seg)
    seg = _RELABEL.sub(relabel, seg)
    seg = seg.replace(BLANK_JA, BLANK_EN)
    seg = _PLAIN.sub(plain, seg)
    # centering offsets: for the original they aligned narrow kanji, for English only
    # eat up the width
    seg = '\n'.join(l for l in seg.split('\n') if not _PAD.match(l))
    return src[:a] + seg + src[b:], n


def names_pass(check):
    """Normalize name variants to CANON across ALL files, not just the items field."""
    hits = 0
    for p in sorted(EN.glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        src = new = p.read_text(encoding='utf-8')
        for a, b in VARIANTS:
            new = new.replace(a, b)
        if new != src:
            n = sum(src.count(a) for a, _ in VARIANTS)
            hits += n
            print(f'  {"Fix needed" if check else "summarized"}: {p.name} ({n})')
            if not check:
                p.write_text(new, encoding='utf-8')
    print(f'name variants {"for editing" if check else "reduced"}: {hits}')
    return hits


def main():
    check = '--check' in sys.argv
    if '--names' in sys.argv:
        return 1 if (names_pass(check) and check) else 0
    total = touched = bad = 0
    for p in sorted(EN.glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        src = p.read_text(encoding='utf-8')
        new, n = fix(src)
        if n:
            total += n
            if new != src:
                touched += 1
                if not check:
                    p.write_text(new, encoding='utf-8')
                print(f'  {"fix needed" if check else "fixed"}: {p.name} ({n} forms)')
        # widths
        r = region(new)
        if r:
            for name in re.findall(r'\(str "([^"]*)"\)', new[r[0]:r[1]]):
                if len(name) > FIELD:
                    bad += 1
                    print(f'  ⚠️ {p.name}: {name!r} wider than the field ({len(name)} > {FIELD})')
    print(f'forms in blocks: {total}; files {"needs fixing" if check else "fixed"}: '
          f'{touched}; wider than field: {bad}')
    return 1 if (check and touched) or bad else 0


if __name__ == '__main__':
    sys.exit(main())
