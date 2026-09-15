#!/usr/bin/env python3
"""One item -- one English name. Check and fix.

Translation was done file by file, and the same Japanese item ended up with different names in
different files: `絶倫草` came up as Stamina Herb, Virility Grass, and Endurance Herb. The player
sees one thing under three names -- in tooltips, in the shop, and in the item window.

The CANON was not pulled out of thin air but taken from THE GAME ITSELF: the name in the item window (`(str "Stam. Herb  ")`,
field is exactly 12 chars) and in the shop menu (`(text "Stamina Herb")`). Prose must match the
menu, and the abbreviation in the window stays an abbreviation -- the field does not stretch.

    python3 tools/terms.py            # show mismatches
    python3 tools/terms.py --fix      # rename per canon

⚠️ After fixing, a relayout (`tools/relayout.py`), a rebuild (`recompile.py`)
and a patch (`make_patch.py`) are required -- line lengths changed.

Don't confuse with `tools/consistency.py`: that one cross-checks IDENTICAL Japanese lines against
each other and does not see a term across different sentences. Plus it skips files whose form count
diverged from the original -- which are all the split floors and ALL combat SENTO*, i.e. exactly
the places where an item is mentioned most often.
"""
import pathlib
import re
import sys

EN = pathlib.Path(__file__).resolve().parent.parent / 'en'

# kanon -> Japanese name, name in the items window (12 characters), encountered variants
ITEMS = {
    'Healing Herb': {
        'ja': '消炎草', 'box': 'Heal Herb',
        'variants': ['Anti-Inflammation Herb', 'Anti-Inflam Herb', 'Anti-Inflam.Herb',
                     'Inflammation Herb', 'Soothing Herb', 'Soothe Grass'],
    },
    'Stamina Herb': {
        'ja': '絶倫草', 'box': 'Stam. Herb',
        'variants': ['Virility Grass', 'Virility Herb', 'Virile Herb', 'Vigor Grass',
                     'Vigor Herb', 'Endurance Herb'],
    },
    'Gold Bar': {'ja': '金塊', 'box': 'Gold Bar', 'variants': ['Gold Nugget', 'Gold Lump']},
    'Ascension Stone': {
        'ja': '飛昇石', 'box': 'Asc. Stone',
        'variants': ['Ascent Stone', 'Rising Stone', 'Soaring Stone'],
    },
}

# Names in parentheses before the line -- this is WHO is speaking. Inconsistency is most noticeable here:
# The player thinks there are two characters. Canon is by majority and by `glossary.json`.
PEOPLE = {
    'Old Man Weiss': {'ja': 'ワイスじいさん', 'variants': ['Weiss Old Man']},
    'Old Man Barvoli': {'ja': 'バルボリじいさん', 'variants': ['Barvoli Old Man']},
}

# Enemy names -- the ones the game prints in the battle window: `(define-proc 41 (<> (text …)))`.
# Edited ONLY within this form, not across the entire text: the description «woman in black» in the line
# do not touch, but the opponent's name is required.
#
# ⚠️ Names like Hinata, Kikuchi, Kenji, Matarou, Butt -- NOT a translation error: in Japanese
# there are exactly 日向, 菊地, 健二, またろう, 尻. Verified by cross-checking all 150 names against the original.
# ⚠️ "Crazy Bear" in SENTO05 is also correct -- there's a クレイジーベア in katakana, a separate opponent.
# He is only incorrect as the SPEAKER in FLOOR00, where the Japanese is 凶暴な大熊 (see SENTENCES).
MONSTERS = {
    # 光の / 影の -- always «Light …» / «Shadow …», never «… of Light»
    'Light Knight': ['Knight of Light'],                  # 光の騎士
    'Light Saint': ['Saint of Light'],                    # 光の聖者
    # 女 -- always «Light Female …», as in 光の女盗賊
    'Light Female Swordsman': ['Light Swordswoman'],      # 光の女剣士
    'Light Female Guard': ['Female Light Guard'],         # 光の女衛兵
    'Light Female Saint': ['Female Light Saint'],         # 光の女聖者
    # 装束 -- always "…-Clad", as in 白装束/紫装束
    'Black-Clad Knight': ['Knight in Black'],             # 黒装束の騎士
    'Black-Clad Woman': ['Woman in Black'],               # 黒装束の女
    'Black-Clad Monk': ['Black-robed Monk'],              # 黒装束の僧侶
    'Black-Clad Sorcerer': ['Black-robed Sorcerer'],      # 黒装束の魔導師
    'Two-Headed Frog': ['Two-headed frog'],               # 双頭のカエル
    'Training Grounds Warrior 2': ['Training Ground Warrior 2'],
    'Club': [' club'],                                    # クラブ -- without a leading space
    'Toad': [' toad'],                                    # トード
}

# Story items. The main one is the very item the game is named after.
THINGS = {
    # ⚠️ The canon is ATTRACTIVE, and this is not a matter of taste. «Stone Tablet of Wordsworth» requires
    # the article, whereas the variants «Wordsworth's …» did without it: a mechanical replacement would have
    # «destroying Stone Tablet of Wordsworth» in 15 replies. The possessive form appears on
    # the position of any variant without modifying neighboring words -- and it is also the literal translation of の.
    "Wordsworth's Stone Tablet": {
        'ja': '『ワーズワースの石板』',
        'variants': ["Stone Tablet of Wordsworth", "Wordsworth's Stele",
                     "Wordsworth's Tablets", "Wordsworth's Tablet",
                     "Wordsworth Tablet", "Wordsworth Slab"],
    },
    'Swordsman\'s Proof': {'ja': '『剣士の証』', 'variants': ["Swordsman's License"]},
}

# ⚠️ Not just a substitution: in some places the number changes too, otherwise it breaks
# «Stamina Herb covers the entire floor». These edits are declared wholesale.
SENTENCES = [
    # number: "Stamina Herb covers the entire floor" -- not in English
    ("Virility Grass covers the entire floor.",
     "Stamina Herbs cover the entire floor."),
    # ⚠️ \\n -- this is TWO characters in the source, not a newline
    ("Fresh Soothe Grass and Stamina Herb\\nin stock",
     "Fresh Healing Herbs and Stamina\\nHerbs in stock"),
    # article: «a Ascension Stone»
    ("used a Rising Stone", "used an Ascension Stone"),
    # the same 持てるだけ has already been translated in FLOOR01 as «as many … as they could carry»
    (" gained as much 'Virility Grass' as possible!!",
     " gathered as many [Stamina Herbs] as they could\\ncarry!!"),
    # «the Tablet of Wordsworth» -- the same thing, but without «Stone»
    ("the Tablet of Wordsworth", "Wordsworth's Stone Tablet"),
    ("the\\nTablet of Wordsworth", "Wordsworth's Stone\\nTablet"),
    # speaker in FLOOR00 -- 凶暴な大熊; opponent クレイジーベア in SENTO05 remains itself
    ("[Crazy Bear]:", "[Ferocious Bear]:"),
    # ⚠️ «stranger» is CORRECT in meaning here (Japanese 変な事 -- «strange»), but reads as
    # the noun "stranger" causes a stumble. 変な -- "weird", and there's no ambiguity.
    ("[Dalk]: I-If you do anything stranger than this...",
     "[Dalk]: I-If you do anything weirder than this..."),
]

# `(define-proc 41 (<> (text "…")))` -- enemy name in the combat window
FOE = re.compile(r'(\(define-proc 41 \(<> \(text ")([^"]*)("\)\)\))')

# ⚠️ «the Wordsworth's Stone Tablet» -- article before possessive. It remained from
# variants like «the Stone Tablet of Wordsworth» and «the Wordsworth Tablet», and without this
# the name-replacement cleanup would have spawned such a pair in 11 replies. `\n` in the source -- TWO characters.
# ⚠️ A `\n` can also appear on the left: «reading\nthe [Wordsworth's …]». In that case, before `the` there is
# the letter `n`, and a normal word boundary `\b` does NOT work there -- two such spots remain.
ARTICLE = re.compile(
    r"""(?:(?<=\\n)|\b)[Tt]he(?: |\\n)+(?=(?:\\"|['"\[])*Wordsworth's Stone Tablet)""")


def files():
    return sorted(p for p in EN.glob('*.MES.rkt') if not p.name.endswith('.orig.rkt'))


def _variants():
    """Variant -> canonical, longer before shorter.

    ⚠️ Order matters: "Inflammation Herb" lies INSIDE "Anti-Inflammation Herb". Shorter
    first and the match doubles, and a correction leaves "Anti-Healing Herb" dangling. 
    """
    pairs = [(v, c) for table in (ITEMS, PEOPLE, THINGS)
             for c, d in table.items() for v in d['variants']]
    return sorted(pairs, key=lambda x: -len(x[0]))


def _rx(variant):
    """A regex variant where space means space OR newline.

    ⚠️ The layout places `\n` right inside the phrase, and line-by-line replacement skips it:
    «Stone Tablet of\nWordsworth» survived a rename in six places and surfaced
    as soon as the next reflow moved the break.
    """
    return re.compile(r'(?: |\\n)'.join(re.escape(w) for w in variant.split(' ')))


def _foes():
    """Enemy name -> canonical."""
    return {v: c for c, vs in MONSTERS.items() for v in vs}


def scan():
    """[(file, line, variant, canonical)] -- all entries referenced by non-canonical names."""
    out, foes = [], _foes()
    for p in files():
        for n, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
            rest = line
            for v, canon in _variants():
                if _rx(v).search(rest):
                    out.append((p.name, n, v, canon))
                    rest = _rx(v).sub('', rest)
            m = FOE.search(line)
            if m and m.group(2) in foes:
                out.append((p.name, n, m.group(2), foes[m.group(2)]))
            if ARTICLE.search(line):
                out.append((p.name, n, "the Wordsworth's Stone Tablet",
                            "Wordsworth's Stone Tablet"))
    return sorted(out)


def fix():
    """Rename per canon. Plural survives: 'Herbs' = 'Herb' + 's'."""
    changed = {}
    for p in files():
        s = was = p.read_text(encoding='utf-8')
        for a, b in SENTENCES:
            s = s.replace(a, b)
        for v, canon in _variants():
            s = _rx(v).sub(canon, s)
        foes = _foes()
        s = FOE.sub(lambda m: m.group(1) + foes.get(m.group(2), m.group(2)) + m.group(3), s)
        s = ARTICLE.sub('', s)
        if s != was:
            p.write_text(s, encoding='utf-8')
            changed[p.name] = sum(1 for x, y in zip(was.split('\n'), s.split('\n')) if x != y)
    return changed


def box_names_still_fit():
    """Item name in the items window is no longer than the field -- otherwise the list will fall apart."""
    bad = [(c, d['box']) for c, d in ITEMS.items() if len(d['box']) > 12]
    return bad


if __name__ == '__main__':
    assert not box_names_still_fit(), box_names_still_fit()
    if '--fix' in sys.argv:
        ch = fix()
        for n, k in sorted(ch.items()):
            print(f'  {n}: lines changed {k}')
        print(f'files renamed: {len(ch)}')
        rest = scan()
        print('mismatches remaining:', len(rest))
        sys.exit(1 if rest else 0)
    rows = scan()
    print(f'mismatches: {len(rows)}')
    for f, n, v, canon in rows:
        print(f'  {f}:{n}  {v!r} -> {canon!r}')
    sys.exit(1 if rows else 0)
