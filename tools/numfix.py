#!/usr/bin/env python3
"""Вернуть пробел там, где число печатается вплотную к слову.

## Что сломано

Число печатается ОТДЕЛЬНОЙ инструкцией, следом за текстом:

    (text "[Dark Fortune Teller]: I see it!!... Your Speed is")
    (text (number (~ M 5)) "!!")

По-японски так и надо -- `すばやさは` кончается частицей, и число липнет к ней законно.
По-английски выходит `Your Speed is34!!`. То же в бою: `HP198 HP restored.`

⚠️ Почему не поймал ни один гейт. Гейт раскладки судит ФОРМУ, а здесь их две, и каждая
сама по себе безупречна. `gate_edges` смотрит знак после маркера `{0}` -- маркера тут нет.
Число вообще не входит в текст формы: `printed()` подставляет на его место `?`, и стык
двух инструкций не проверял никто. Найдено игроком на кадре, 2026-09-15.

⚠️ Кадры это ВИДЕЛИ и были прочитаны -- `|HP1?? was restored.|`, -- но `screenqa` отметил
только «нечитаемое знакоместо» (цифры 6-9 не в эталонах шрифта), и слипшийся текст рядом
остался незамеченным. Отчёт, который прячет находку за соседней жалобой, -- плохой отчёт.

## Где пробел НЕ нужен

Если между текстом и числом переставлен курсор (`set-arr~ @ 17` -- строка/колонка окна,
`@ 21` -- координаты в меню), то число печатается в СВОЕЙ позиции, и пробел только сдвинет
колонку. Это меню магазина (`Healing Herb` … цена) и панель. Такие места не трогаются --
признак берётся из кода, а не из списка имён файлов.

    tools/numfix.py            # показать
    tools/numfix.py --apply    # записать и пересобрать
"""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from split import close                                             # noqa: E402
import gates                                                        # noqa: E402

EN = ROOT / 'en'

# ⚠️ Пробела МАЛО. Вставить его в `"HP" + N + " HP restored."` -- значит получить
# «HP 198 HP restored.» с двойным HP, а в `"…'s HP" + N + " point recovered!!"` --
# «…'s HP 8 point recovered!!». Японский ставит число между частицами, английский так не
# умеет; фразу надо ПЕРЕСОБРАТЬ вокруг числа. Пары «что было до числа / что после» ->
# «что станет». Список не выдуман: он снят пересчётом всех 97 мест.
PHRASES = {
    ('HP', ' was restored.'):            ('HP restored by ', '.'),
    ('HP', ' HP restored.'):             ('HP restored by ', '.'),
    ('HP was', ' restored.'):            ('HP restored by ', '.'),
    ('HP was', ' recovered.'):           ('HP restored by ', '.'),
    ('HP of', ' was restored.'):         ('HP restored by ', '.'),
    ('HP by', ' was restored.'):         ('HP restored by ', '.'),
    ('HP by', ' HP restored.'):          ('HP restored by ', '.'),
    ('HP', ' was recovered.'):           ('HP restored by ', '.'),
    ('HP', ' recovered some HP.'):       ('HP restored by ', '.'),
    ('HP', "'s HP was restored."):       ('HP restored by ', '.'),
    ("The Saint of Light's HP", ' point recovered!!'):
        ("The Saint of Light's HP restored by ", '!!'),
    ("The Female Light Saint's HP", ' recovered a point!!'):
        ("The Female Light Saint's HP restored by ", '!!'),
    ("The Woman in Black's HP", ' point recovered!!'):
        ("The Woman in Black's HP restored by ", '!!'),
    ("The Dark Messenger's HP", ' point recovered!!'):
        ("The Dark Messenger's HP restored by ", '!!'),
    ("The Black-robed Monk's HP", ' point recovered!!'):
        ("The Black-robed Monk's HP restored by ", '!!'),
    ("The Shadow Saint's HP", ' point recovered!!'):
        ("The Shadow Saint's HP restored by ", '!!'),
}

# Курсор переставлен -> число идёт в свою колонку, слипнуться не с чем.
MOVED = re.compile(r'set-arr~ @ (?:17|21)\b')
# Печать прервана -> число начнёт новую строку или новое окно.
BREAKS = re.compile(r'\(wait|\(clear|proc 28\b|proc 10\b')


def sites(src):
    """[(спан литерала ДО числа, его текст, спан литерала ПОСЛЕ, его текст)]."""
    spans = [(m.start(), close(src, m.start()) + 1) for m in re.finditer(r'\(text\b', src)]
    out = []
    for (a, e), (na, ne) in zip(spans, spans[1:]):
        if not re.match(r'\(text\s*\(number', src[na:ne]):
            continue
        between = src[e:na]
        if MOVED.search(between) or BREAKS.search(between):
            continue
        lits = list(re.finditer(r'"((?:[^"\\]|\\.)*)"', src[a:e]))
        if not lits:
            continue
        last = lits[-1]
        tail = last.group(1)
        if not tail or tail.endswith((' ', '\\n')):
            continue
        aft = list(re.finditer(r'"((?:[^"\\]|\\.)*)"', src[na:ne]))
        head = aft[0].group(1) if aft else ''
        out.append(((a + last.start(1), a + last.end(1), tail),
                    ((na + aft[0].start(1), na + aft[0].end(1), head) if aft else None)))
    return out


def rewrite(tail, head):
    """Как переписать пару вокруг числа. None -- хватит одного пробела."""
    key = (tail.strip(), head)
    if key in PHRASES:
        return PHRASES[key]
    return None


def main(names, apply):
    total, touched = 0, []
    for name in names:
        src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
        rows = sites(src)
        if not rows:
            continue
        total += len(rows)
        touched.append(name)
        edits = []
        for (ba, be, tail), aft in rows:
            new = rewrite(tail, aft[2] if aft else '')
            if new and aft:
                edits.append((ba, be, new[0]))
                edits.append((aft[0], aft[1], new[1]))
                shown = f'{new[0]!r} + число + {new[1]!r}'
            else:
                edits.append((ba, be, tail + ' '))
                shown = f'{tail[-30:] + " "!r} + число'
            if len(touched) <= 4 and len(edits) <= 4:
                print(f'  {name:16} …{tail[-30:]!r} + число  ->  {shown}')
        if apply:
            for a2, b2, txt in sorted(edits, reverse=True):
                src = src[:a2] + txt + src[b2:]
            (EN / f'{name}.rkt').write_text(src, encoding='utf-8')
    print(f'\nмест: {total}, файлов: {len(touched)}')
    if not apply:
        print('НЕ ЗАПИСАНО. Применить: tools/numfix.py --apply')
        return 0
    print('\nпересборка:')
    bad = 0
    for name in touched:
        gates.juice(['-cf', f'{name}.rkt'], EN)
        mes = EN / f'{name}.rkt.mes'
        n = mes.stat().st_size if mes.exists() else 0
        over = ' ⚠️ ЗА ПОРОГОМ' if n > gates.MES_MAX else ''
        if not n or over:
            bad += 1
            print(f'  {name:16} {n} б{over}', flush=True)
    print(f'{"❌ провалов: " + str(bad) if bad else "✅ все пересобрались под порогом"}')
    return 1 if bad else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    ns = a.names or sorted(p.name[:-4] for p in EN.glob('*.MES.rkt')
                           if not p.name.endswith('.orig.rkt'))
    sys.exit(main(ns, a.apply))
