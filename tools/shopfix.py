#!/usr/bin/env python3
"""Shop dialogue: strip the number-width padding and rebuild the purchase question from scratch.

## Two defects, both visible to the player in the shop

**1. Padding for an unknown-width number.** `render.pad_breaks` pads the line out to the window
edge with spaces, treating `(number …)` as six characters wide. On screen the number can be
anything: with `260` the line comes out three characters shorter, the engine stretches it with
the next word, and it tears at the window edge. Frame `emu/replay/3850_return_key.png`:

    [Item Shop Owner]: All together that's 260 gold, ya  kno
    w...

The gap in the middle of the line is that padding. The padding itself can't be fixed: the
number's width is only known in-game. So the lines are shortened so that a FIVE-DIGIT number
never needs to wrap at all -- then there's no padding, and nothing to tear. Threshold is
`render.LINE` (55).

**2. The purchase question is assembled from independently translated halves.** The opening
quote is in one form, the closing one in eight `(if (== V n) …)` branches, the tail in the
ninth. The halves were translated separately, so the quote opened with `'` and closed with `"`,
word order fell apart, and in SHP_5I a chunk of the question landed in the item slot:

    [Item Shop Owner]: ' all Healing Herbs'  will you buy?
    [Item Shop Owner]: ' Gold Bar", will you buy max\n?

Rebuilt from scratch on one schema for all six shops: `<Who>: Buy 'Item'?` and
`<Who>: Buy all 'Items'?`.

⚠️ ONLY line contents change -- not a single instruction is added or removed, so the
structural gate (`gates.gate_compile_and_structure`) sees the same skeleton.

    tools/shopfix.py --check    # show what would be replaced
    tools/shopfix.py --apply    # write it
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EN = ROOT / 'en'

# ---------------------------------------------------------------- money phrases
# (file, old, new, how many times expected)
MONEY = [
    ('KANKIN', '" gold total... Come back  when you find more."',
     '" gold in all. Come back."', 2),
    ('SHP_0A', '" gold... good price if you ask  me."',
     '" gold... a good price, eh?"', 1),
    ('SHP_0A', '" gold... a bit steep if you ask me."',
     '" gold... a bit steep, eh?"', 1),
    ('SHP_0B', '" gold. A good     deal, right?"', '" gold. Good deal?"', 1),
    ('SHP_2A', '" gold... a good    deal, right?"', '" gold. Fair deal?"', 1),
    ('SHP_2A', '" gold... a good deal,  right?"', '" gold... good deal?"', 1),
    ('SHP_2A', '" gold... Pretty cheap, huh."', '" gold... Cheap, huh?"', 1),
    ('SHP_3I', '" gold total,        though..."', '" gold, though..."', 4),
    ('SHP_4I', '"[Item Shop Owner]: All together that\'s "',
     '"[Item Shop Owner]: That\'s "', 4),
    ('SHP_4I', '" gold, ya  know..."', '" gold, ya know..."', 4),
    ('SHP_4S', '" gold... cheap,       right?"', '" gold... cheap, eh?"', 1),
    ('SHP_5B', '" gold for that one."', '" gold for it."', 1),
]

# ---------------------------------------------------------------- purchase question
# One schema for all benches: <Who>: Buy 'Item'?  /  <Who>: Buy all 'Items'?
ITEMS = {
    0: "'Healing Herb'",         1: "all 'Healing Herbs'",
    2: "'Stamina Herb'",         3: "all 'Stamina Herbs'",
    4: "'Gold Bar'",             5: "all 'Gold Bars'",
    6: "'Ascension Stone'",      7: "all 'Ascension Stones'",
}
BUY = {
    'SHP_0I': ('"[Epo]: \'"', '"[Epo]: Buy "', '" Buy it?"', ITEMS),
    'SHP_2I': ('"[Item Shop Owner]: \'"', '"[Item Shop Owner]: Buy "',
               '" will you\\nbuy?"', ITEMS),
    'SHP_3I': ('"[Item Shop Owner]: \'"', '"[Item Shop Owner]: Buy "',
               '" for me?"', ITEMS),
    'SHP_4I': ('"[Item Shop Owner]: \'"', '"[Item Shop Owner]: Buy "',
               '" will you buy?"', ITEMS),
    'SHP_4S': ('"[Dark Merchant]: \'"', '"[Dark Merchant]: Buy "',
               '" you\'re buying, I\\nsee."',
               ITEMS | {4: "'Magic Boots'", 5: "all 'Magic Boots'"}),
    'SHP_5I': ('"[Item Shop Owner]: \'"', '"[Item Shop Owner]: Buy "',
               '"\\n?"', ITEMS),
}
# Legacy branch texts — as they are currently stored, per file. Key is the branch number.
OLD_BRANCH = {
    'SHP_0I': {0: '"Healing Herb\' "', 1: '" all Healing Herbs\' "',
               2: '"Stamina Herb\\""', 3: '" all Stamina Herbs\\""',
               4: '" Gold Bar\\""', 5: '" all Gold Bars\\""',
               6: '" Ascension Stone\\""', 7: '" all Ascension Stones\\""'},
    'SHP_2I': {0: '"Healing Herb\' "', 1: '" all Healing Herbs\' "',
               2: '" Stamina Herb\\""', 3: '" all Stamina Herbs\\""',
               4: '" Gold Bar\\""', 5: '" all Gold Bars\\""',
               6: '" Ascension Stone\\""', 7: '" all Ascension Stones\\""'},
    'SHP_3I': {0: '"Healing Herb\' "', 1: '" all Healing Herbs\' "',
               2: '" Stamina Herb\\""', 3: '" all Stamina Herbs\\""',
               4: '" Gold Bar\\""', 5: '" as many Gold Bars\'"',
               6: '" Ascension Stone\\""', 7: '" as many Ascension Stones\'"'},
    'SHP_4I': {0: '"Healing Herb\' "', 1: '" all Healing Herbs\' "',
               2: '" Stamina Herb\\""', 3: '" all Stamina Herbs\\""',
               4: '" Gold Bar\\""', 5: '" as many Gold Bars as you can\\ncarry\'"',
               6: '" Ascension Stone\\""',
               7: '" as many Ascension Stones as you\\ncan carry\'"'},
    'SHP_4S': {0: '"Healing Herb\' "', 1: '" all Healing Herbs\' "',
               2: '" Stamina Herb\\""', 3: '" all Stamina Herbs\\""',
               4: '" Magic Boots\'"', 5: '"All Magic Boots\'"',
               6: '" Ascension Stone\\""', 7: '"All Ascension Stone\'"'},
    'SHP_5I': {0: '"Healing Herb\' "', 1: '" all Healing Herbs\' "',
               2: '" Stamina Herb\\""', 3: '" all Stamina Herbs\\""',
               4: '" Gold Bar\\""', 5: '" Gold Bar\\", will you buy max"',
               6: '" Ascension Stone\\""',
               7: '" Ascension Stone\\", will you buy max"'},
}

# ---------------------------------------------------------------- sale question
# A nit of the same kind: a leading space inside quotes and a space before the closing one.
SELL = [
    ('SHP_0I', '(text " \' - will you sell it?")', '(text "\' - will you sell it?")', 1),
    ('SHP_2I', '(if (== V 3) (<> (text " Ascension Stone")))',
     '(if (== V 3) (<> (text "Ascension Stone")))', 1),
]

# ---------------------------------------------------------------- "used item" in combat
# Same class: <name> + copula + <object> + tail. The copula `は、` remained JAPANESE -- it
# one of the few forms where the Japanese character is not visible to the charset gate, because the form is short
# and consists of control characters. The screen displayed `Astralは、Gold Bar tried using it!!`.
# Rearranged in the order that gives the combo: `Astral tried using 'Gold Bar'!!`.
USED = [
    ('SENTO04', '(text "は、")', '(text " tried using \'")', 1),
    ('SENTO04', '(text " tried using it!!")', '(text "\'!!")', 1),
    ('SENTO0G', '(text "は、")', '(text " tried using \'")', 1),
    ('SENTO0G', '(text " tried using it!!")', '(text "\'!!")', 1),
    ('SENTO0J', '(text "は、")', '(text " tried using \'")', 1),
    ('SENTO0J', '(text " tried using!")', '(text "\'!")', 1),
]


def edits():
    """[(file, old, new, count)] -- all replacements in one list.

    ⚠️ The opening form `(text "[Item Shop Owner]: '")` appears in the file TWICE -- in the
    buy question and in the sell question. So it is replaced TOGETHER with the first branch:
    this pair is unique in the file.
    """
    out = list(MONEY)
    for fn, (lead_old, lead_new, tail_old, items) in BUY.items():
        pairs = OLD_BRANCH[fn]
        for v, old in pairs.items():
            branch_old = f'(if (== V {v}) (<> (text {old})))'
            branch_new = f'(if (== V {v}) (<> (text "{items[v]}")))'
            if v == 0:
                out.append((fn, f'(text {lead_old})\n         {branch_old}',
                            f'(text {lead_new})\n         {branch_new}', 1))
            else:
                out.append((fn, branch_old, branch_new, 1))
        out.append((fn, f'(text {tail_old})', '(text "?")', 1))
    return out + SELL + USED


def main(apply):
    bad = 0
    by_file = {}
    for fn, old, new, want in edits():
        by_file.setdefault(fn, []).append((old, new, want))
    for fn, rows in sorted(by_file.items()):
        p = EN / f'{fn}.MES.rkt'
        src = p.read_text(encoding='utf-8')
        done = 0
        for old, new, want in rows:
            got = src.count(old)
            if got != want:
                # ⚠️ IDEMPOTENCY. Old text is gone, new text is in place -- the edit has already
                # applied, and this is not a failure: otherwise the re-run (which is inevitable, edits
                # come in waves) declares broken what it itself fixed.
                if got == 0 and src.count(new) >= want:
                    done += 1
                    continue
                print(f'  ❌ {fn}: {old[:56]} -- found {got}, expected {want}')
                bad += 1
                continue
            src = src.replace(old, new)
        if apply and not bad:
            p.write_text(src, encoding='utf-8')
        note = f'replaced {len(rows) - done}' + (f', already done {done}' if done else '')
        print(f'  {"✅" if not bad else "⏭️"} {fn}: {note}')
    if bad:
        sys.exit(f'\n❌ mismatches: {bad} -- nothing written')
    print(f'\n{"written" if apply else "NOT WRITTEN (--apply)"}: files {len(by_file)}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    main(ap.parse_args().apply)
