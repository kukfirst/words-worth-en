#!/usr/bin/env python3
"""Один предмет -- одно английское имя. Проверка и правка.

Перевод шёл файл за файлом, и один и тот же японский предмет получал разные имена в разных
файлах: `絶倫草` побывал и Stamina Herb, и Virility Grass, и Endurance Herb. Игрок видит
одну вещь под тремя названиями -- в подсказках, в лавке и в окне предметов.

КАНОН взят не с потолка, а из САМОЙ ИГРЫ: имя в окне предметов (`(str "Stam. Herb  ")`,
поле ровно 12 знаков) и в меню лавки (`(text "Stamina Herb")`). Проза обязана совпадать с
меню, а сокращение в окне остаётся сокращением -- поле не тянется.

    python3 tools/terms.py            # показать расхождения
    python3 tools/terms.py --fix      # переименовать по канону

⚠️ После правки нужен пересчёт раскладки (`tools/relayout.py`), пересборка (`recompile.py`)
и патча (`make_patch.py`) -- длина строк изменилась.

Не путать с `tools/consistency.py`: та сверяет ОДИНАКОВЫЕ японские реплики между собой и
термина внутри разных предложений не видит. Плюс она пропускает файлы, у которых число форм
разошлось с оригиналом -- а это все разрезанные этажи и ВСЕ боевые SENTO*, то есть ровно те
места, где предмет и упоминается чаще всего.
"""
import pathlib
import re
import sys

EN = pathlib.Path(__file__).resolve().parent.parent / 'en'

# канон -> японское имя, имя в окне предметов (12 знаков), встреченные варианты
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

# Имена в скобках перед репликой -- это то, КТО говорит. Разнобой здесь заметнее всего:
# игрок думает, что персонажей двое. Канон -- по большинству и по `glossary.json`.
PEOPLE = {
    'Old Man Weiss': {'ja': 'ワイスじいさん', 'variants': ['Weiss Old Man']},
    'Old Man Barvoli': {'ja': 'バルボリじいさん', 'variants': ['Barvoli Old Man']},
}

# Имена противников -- те, что игра печатает в боевом окне: `(define-proc 41 (<> (text …)))`.
# Правятся ТОЛЬКО внутри этой формы, не по всему тексту: описание «woman in black» в реплике
# трогать нельзя, а имя противника -- нужно.
#
# ⚠️ Имена вроде Hinata, Kikuchi, Kenji, Matarou, Butt -- НЕ ошибка перевода: в японском
# там ровно 日向, 菊地, 健二, またろう, 尻. Проверено сверкой всех 150 имён с оригиналом.
# ⚠️ «Crazy Bear» в SENTO05 тоже верен -- там クレイジーベア катаканой, отдельный противник.
# Неверен он только как ГОВОРЯЩИЙ в FLOOR00, где японский -- 凶暴な大熊 (см. SENTENCES).
MONSTERS = {
    # 光の / 影の -- всегда «Light …» / «Shadow …», а не «… of Light»
    'Light Knight': ['Knight of Light'],                  # 光の騎士
    'Light Saint': ['Saint of Light'],                    # 光の聖者
    # 女 -- всегда «Light Female …», как в 光の女盗賊
    'Light Female Swordsman': ['Light Swordswoman'],      # 光の女剣士
    'Light Female Guard': ['Female Light Guard'],         # 光の女衛兵
    'Light Female Saint': ['Female Light Saint'],         # 光の女聖者
    # 装束 -- всегда «…-Clad», как в 白装束/紫装束
    'Black-Clad Knight': ['Knight in Black'],             # 黒装束の騎士
    'Black-Clad Woman': ['Woman in Black'],               # 黒装束の女
    'Black-Clad Monk': ['Black-robed Monk'],              # 黒装束の僧侶
    'Black-Clad Sorcerer': ['Black-robed Sorcerer'],      # 黒装束の魔導師
    'Two-Headed Frog': ['Two-headed frog'],               # 双頭のカエル
    'Training Grounds Warrior 2': ['Training Ground Warrior 2'],
    'Club': [' club'],                                    # クラブ -- без ведущего пробела
    'Toad': [' toad'],                                    # トード
}

# Вещи сюжета. Главная из них -- та самая, в честь которой названа игра.
THINGS = {
    # ⚠️ Канон ПРИТЯЖАТЕЛЬНЫЙ, и это не вкусовщина. «Stone Tablet of Wordsworth» требует
    # артикля, а варианты «Wordsworth's …» обходились без него: механическая замена дала бы
    # «destroying Stone Tablet of Wordsworth» в 15 репликах. Притяжательная форма встаёт на
    # место любого варианта без правки соседних слов -- и она же буквальный перевод の.
    "Wordsworth's Stone Tablet": {
        'ja': '『ワーズワースの石板』',
        'variants': ["Stone Tablet of Wordsworth", "Wordsworth's Stele",
                     "Wordsworth's Tablets", "Wordsworth's Tablet",
                     "Wordsworth Tablet", "Wordsworth Slab"],
    },
    'Swordsman\'s Proof': {'ja': '『剣士の証』', 'variants': ["Swordsman's License"]},
}

# ⚠️ Не просто подстановка: у некоторых мест меняется и число, иначе выходит
# «Stamina Herb covers the entire floor». Эти правки объявлены целиком.
SENTENCES = [
    # число: «Stamina Herb covers the entire floor» -- не по-английски
    ("Virility Grass covers the entire floor.",
     "Stamina Herbs cover the entire floor."),
    # ⚠️ \\n -- это ДВА знака в исходнике, а не перевод строки
    ("Fresh Soothe Grass and Stamina Herb\\nin stock",
     "Fresh Healing Herbs and Stamina\\nHerbs in stock"),
    # артикль: «a Ascension Stone»
    ("used a Rising Stone", "used an Ascension Stone"),
    # то же японское 持てるだけ уже переведено в FLOOR01 как «as many … as they could carry»
    (" gained as much 'Virility Grass' as possible!!",
     " gathered as many [Stamina Herbs] as they could\\ncarry!!"),
    # «the Tablet of Wordsworth» -- та же вещь, но без «Stone»
    ("the Tablet of Wordsworth", "Wordsworth's Stone Tablet"),
    ("the\\nTablet of Wordsworth", "Wordsworth's Stone\\nTablet"),
    # говорящий в FLOOR00 -- 凶暴な大熊; противник クレイジーベア в SENTO05 остаётся собой
    ("[Crazy Bear]:", "[Ferocious Bear]:"),
    # ⚠️ «stranger» здесь ВЕРНО по смыслу (японское 変な事 -- «странное»), но читается как
    # существительное «незнакомец» и спотыкает. 変な -- «weird», и двусмысленности нет.
    ("[Dalk]: I-If you do anything stranger than this...",
     "[Dalk]: I-If you do anything weirder than this..."),
]

# `(define-proc 41 (<> (text "…")))` -- имя противника в боевом окне
FOE = re.compile(r'(\(define-proc 41 \(<> \(text ")([^"]*)("\)\)\))')

# ⚠️ «the Wordsworth's Stone Tablet» -- артикль перед притяжательным. Он оставался от
# вариантов вроде «the Stone Tablet of Wordsworth» и «the Wordsworth Tablet», и без этой
# чистки замена имени плодила бы такую пару в 11 репликах. `\n` в исходнике -- ДВА знака.
# ⚠️ Слева тоже может стоять `\n`: «reading\nthe [Wordsworth's …]». Перед `the` тогда лежит
# буква `n`, и обычная граница слова `\b` там НЕ срабатывает -- два таких места и остались.
ARTICLE = re.compile(
    r"""(?:(?<=\\n)|\b)[Tt]he(?: |\\n)+(?=(?:\\"|['"\[])*Wordsworth's Stone Tablet)""")


def files():
    return sorted(p for p in EN.glob('*.MES.rkt') if not p.name.endswith('.orig.rkt'))


def _variants():
    """Вариант -> канон, длинные раньше коротких.

    ⚠️ Порядок значим: «Inflammation Herb» лежит ВНУТРИ «Anti-Inflammation Herb». Коротким
    вперёд и находка двоится, и правка оставляет висеть «Anti-Healing Herb».
    """
    pairs = [(v, c) for table in (ITEMS, PEOPLE, THINGS)
             for c, d in table.items() for v in d['variants']]
    return sorted(pairs, key=lambda x: -len(x[0]))


def _rx(variant):
    """Вариант как регулярка, где пробел -- это пробел ИЛИ перенос строки.

    ⚠️ Раскладка ставит `\n` прямо внутри фразы, и построчная замена такую пропускает:
    «Stone Tablet of\nWordsworth» пережил переименование в шести местах и вылез наружу,
    как только следующая перекладка передвинула разрыв.
    """
    return re.compile(r'(?: |\\n)'.join(re.escape(w) for w in variant.split(' ')))


def _foes():
    """Имя противника -> канон."""
    return {v: c for c, vs in MONSTERS.items() for v in vs}


def scan():
    """[(файл, строка, вариант, канон)] -- всё, что зовётся не по канону."""
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
    """Переименовать по канону. Множественное число выживает: 'Herbs' = 'Herb' + 's'."""
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
    """Имя в окне предметов не длиннее поля -- иначе список разъедется."""
    bad = [(c, d['box']) for c, d in ITEMS.items() if len(d['box']) > 12]
    return bad


if __name__ == '__main__':
    assert not box_names_still_fit(), box_names_still_fit()
    if '--fix' in sys.argv:
        ch = fix()
        for n, k in sorted(ch.items()):
            print(f'  {n}: строк изменено {k}')
        print(f'файлов переименовано: {len(ch)}')
        rest = scan()
        print('осталось расхождений:', len(rest))
        sys.exit(1 if rest else 0)
    rows = scan()
    print(f'расхождений: {len(rows)}')
    for f, n, v, canon in rows:
        print(f'  {f}:{n}  {v!r} -> {canon!r}')
    sys.exit(1 if rows else 0)
