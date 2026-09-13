#!/usr/bin/env python3
"""Починить поле названия предмета — в поле и во всех боевых сценах.

    python3 tools/itembox.py [--check]

Один и тот же список предметов лежит в 28 файлах: `START.MES`, `START1.MES` и 26 боевых
`SENTO*`. В каждом он рисуется через `(set-arr~ @ 17 2 232)` и страдает двумя болезнями.

**Болезнь 1 — словарь.** `(text …)` компилируется через словарь `.MES` (128 частых
символов, однобайтовые индексы), а это поле словарь НЕ разжимает: байты индексов
складываются в пары и уходят в знакогенератор кандзи. На экране — половинки иероглифов.
`(str …)` пишет литералом и минует словарь. Японский оригинал уцелел случайно: его
словарь — четыре символа, ни один в названия не попадал. Подробности: STATUS.md §13.

**Болезнь 2 — разнобой.** Файлы переводились независимо, и предмет 902 назван семью
способами (`Gold Bar`, `Gold Chunk`, `Gold Ingot`, `Gold Lump`, `Gold Nugget`,
`Lump of Gold`, `Gold Ingots`). Здесь имя выбирается по регистру, а не по тому, что
написал переводчик.

⚠️ Ширина поля — 14 узких знакомест (`x=16..128`, дальше рамка панели). Замер по
НЕТРОНУТОМУ японскому образу: там `消炎草　08` рисуется прямо на панели, а собственная
зачистка оригинала (7 иероглифических пробелов) и задаёт эти 14. Счётчик из двух цифр
прижат к знакоместам 13–14, поэтому имя со счётчиком добивается до 12.

⚠️ Полные названия остаются в лавке и репликах — там ширины хватает и видеть предмет
целиком удобнее. Сокращения живут только здесь. Это решение, а не недоделка.

⚠️ Ряд пробелов одним литералом уже убивал игру (замер: 50 валит, 12 живёт, потолок 16),
поэтому зачистка — два литерала по 7, а не один из 14.

Проход идемпотентен: `recompile.py` переписывает `en/*.rkt` на месте, и повторный запуск
не должен ничего менять.
"""
import pathlib, re, sys

EN = pathlib.Path(__file__).resolve().parent.parent / 'en'
FIELD = 14                      # знакомест в поле
COUNT = 2                       # разрядов счётчика

NUM_ON  = '(set-arr~ @ 20 (// (&& (~ @ 20) 4095) 4096))'
NUM_OFF = '(set-arr~ @ 20 (&& (~ @ 20) 4095))'
BLANK_JA = '(text "　　　　　　　")'
BLANK_EN = '(str "       ") (str "       ")'          # 14 знакомест двумя литералами

# Ярлык поля по регистру предмета. Сокращения выведены из канонических полных названий,
# которые остаются в лавке и репликах: Healing Herb / Stamina Herb / Gold Ingot /
# Ascension Stone.
# Канонические названия расходуемых предметов. Файлы переводились независимо, и один и
# тот же предмет получил до ПЯТИ разных имён: 902 звался Gold Lump / Gold Ingot /
# Gold Nugget / Lump of Gold / Gold Chunk. Здесь имя одно — и выбран САМЫЙ КОРОТКИЙ из
# ходивших вариантов: правка тогда только укорачивает файлы, а рост — единственное, чем
# можно случайно упереться в порог буфера скрипта (40 000 б) или в ширину окна.
CANON = {900: 'Healing Herb', 901: 'Stamina Herb', 902: 'Gold Bar', 903: 'Ascension Stone'}

# вариант -> канон. Множественное идёт первым: иначе «Gold Nuggets» после замены
# единственного превратится в «Gold Bars» через «Gold Bar»+«s» лишь по счастливой
# случайности, а «Lumps of Gold» не совпадёт вовсе.
VARIANTS = [
    # ⚠️ Написания, которые приносит canon.py: он решает по ОБРЫВКУ, а не по предмету, и
    # для 消炎草/絶倫草 выбирал независимо в разных местах -- отсюда Soothe/Vigor/Vigor Grass
    # рядом с уже известными Soothing/Virile. Длинные фразы идут первыми: замена идёт по
    # списку подряд, и «Vigor Grass» иначе съело бы хвост «Endless Vigor Grass».
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

# Ярлык поля: сокращение канонического названия, потому что здесь всего 14 знакомест.
LABEL = {900: 'Heal Herb', 901: 'Stam. Herb', 902: 'Gold Bar', 903: 'Asc. Stone'}

_COUNTED = re.compile(r'\(text "([^"]*)" \(number \(: (\d+)\)\)\)')
# уже сконвертированный ярлык -- чтобы прогон был идемпотентным И подхватывал смену имени
_RELABEL = re.compile(r'\(str "([^"]*)"\) \(text \(number \(: (\d+)\)\)\)')
_PLAIN = re.compile(r'\(text "([^"]+)"\)')
_PAD = re.compile(r'^\s*\(str " +"\)\s*$')


def region(src):
    """Границы cond со списком предметов: от ветки 0 до конца объемлющего cond."""
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
    # отбивки центрирования: оригиналу они выравнивали узкие кандзи, английскому только
    # съедают ширину
    seg = '\n'.join(l for l in seg.split('\n') if not _PAD.match(l))
    return src[:a] + seg + src[b:], n


def names_pass(check):
    """Свести варианты названий к CANON по ВСЕМ файлам, а не только в поле предметов."""
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
            print(f'  {"нужна правка" if check else "сведено"}: {p.name} ({n})')
            if not check:
                p.write_text(new, encoding='utf-8')
    print(f'вариантов названий {"к правке" if check else "сведено"}: {hits}')
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
                print(f'  {"нужна правка" if check else "поправлен"}: {p.name} ({n} форм)')
        # ширины
        r = region(new)
        if r:
            for name in re.findall(r'\(str "([^"]*)"\)', new[r[0]:r[1]]):
                if len(name) > FIELD:
                    bad += 1
                    print(f'  ⚠️ {p.name}: {name!r} шире поля ({len(name)} > {FIELD})')
    print(f'форм в блоках: {total}; файлов {"требует правки" if check else "поправлено"}: '
          f'{touched}; шире поля: {bad}')
    return 1 if (check and touched) or bad else 0


if __name__ == '__main__':
    sys.exit(main())
