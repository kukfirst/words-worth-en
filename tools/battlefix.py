#!/usr/bin/env python3
"""Развернуть боевые строки, где имя получателя удара названо нанёсшим.

## Что сломано

В бою имя печатает `(proc 41)`, а следом идёт форма. Японская форма начинается с `に` --
частицы, которая делает названное имя ПОЛУЧАТЕЛЕМ:

    (proc 41) (text "に" (number D) "のダメージを与えた！！")     [ИМЯ] получил D урона

Английский на том же месте говорит обратное:

    (proc 41) (text " dealt " (number D) " damage!!")            [ИМЯ] нанёс D урона

Кадр из эмулятора, которым это поймано: `A Sturdy Dwarf dealt 0 damage to!!` -- Дворф там
получал, а не наносил, и вдобавок `to` повисло без имени, потому что вставлять после формы
нечего.

Замер: мест, где японское `に` делает имя получателем, -- **52**; переведено верно 7,
**перевёрнуто 45** в 23 боевых файлах из 26. Это самая частая строка в игре: по одной на
каждый удар.

⚠️ Почему это не поймал ни один гейт. `gate_edges` смотрит знак ПОСЛЕ маркера, а маркера
здесь нет вовсе -- имя печатает процедура. `gate_width` меряет ширину. Структурный гейт
сверяет скелет инструкций, а он не менялся. Смысл фразы не проверял никто, и проверить его
можно было только одним способом -- прочитав кадр.

## Как чинится

Замена по форме, не по смыслу: «нанёс» -> «получил», висячее `to` убирается. Правится
ТОЛЬКО там, где японское `に` подтверждает роль получателя -- список мест считается, а не
пишется руками.

    tools/battlefix.py            # показать
    tools/battlefix.py --apply    # записать и пересобрать
"""
import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from split import close                                             # noqa: E402
import gates                                                        # noqa: E402

EN = ROOT / 'en'

# Форма -> исправленная форма. Слева ровно то, что нашлось замером.
SWAP = [
    (' dealt ', ' took '),
    (' did no damage', ' took no damage'),
    (' dealt no damage', ' took no damage'),
    # ⚠️ Одиночная форма: кто-то ужимал её под размер и срезал глагол вовсе.
    ('- no damage', ' took no damage'),
]
# Висячий хвост: вставлять после формы нечего, имя уже напечатано ДО неё.
TAIL = [(' damage to it!!', ' damage!!'), (' damage to!!', ' damage!!'),
        (' damage to"', ' damage"'), (' damage to', ' damage')]


def sites(src):
    """(начало, конец, текст) ближайшего (text …) после каждого (proc 41).

    По балансу скобок, а не регулярным выражением: между ними стоит починка цифр
    `(set-arr~ @ 20 (// (&& (~ @ 20) 4095) 4096))` с тремя уровнями вложенности.
    """
    out = []
    for m in re.finditer(r'\(proc 41\)', src):
        i = src.find('(text', m.end())
        if i < 0 or i - m.end() > 300:
            out.append(None)
            continue
        e = close(src, i)
        out.append((i, e + 1, src[i:e + 1]))
    return out


def fix(form):
    """Исправленная форма или None, если менять нечего."""
    out = form
    for a, b in SWAP:
        if a in out:
            out = out.replace(a, b, 1)
            break
    else:
        return None
    for a, b in TAIL:
        out = out.replace(a, b)
    return out if out != form else None


def plan(name):
    """Что поменяется в файле: список (начало, конец, было, стало)."""
    orig = EN / f'{name}.orig.rkt'
    if not orig.exists():
        return []
    ja = sites(orig.read_text(encoding='utf-8'))
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    en = sites(src)
    if len(ja) != len(en):
        return []
    out = []
    for a, b in zip(ja, en):
        if not a or not b:
            continue
        # ⚠️ Правим ТОЛЬКО там, где японское `に` подтверждает: имя -- получатель.
        if '"に' not in a[2][:10]:
            continue
        new = fix(b[2])
        if new:
            out.append((b[0], b[1], b[2], new))
    return out


def main(names, apply):
    total, touched = 0, []
    for name in names:
        rows = plan(name)
        if not rows:
            continue
        total += len(rows)
        touched.append(name)
        if len(touched) <= 3:
            for _, _, was, now in rows:
                print(f'  {name}')
                print(f'      было : {was}')
                print(f'      стало: {now}')
        if apply:
            src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
            for s, e, _, now in sorted(rows, reverse=True):
                src = src[:s] + now + src[e:]
            (EN / f'{name}.rkt').write_text(src, encoding='utf-8')
    print(f'\nмест: {total}, файлов: {len(touched)}')
    if not apply:
        print('НЕ ЗАПИСАНО. Применить: tools/battlefix.py --apply')
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
    print(f'\n{"❌ провалов: " + str(bad) if bad else "✅ все пересобрались под порогом"}')
    return 1 if bad else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    ns = a.names or sorted(p.name[:-4] for p in EN.glob('*.MES.rkt')
                           if not p.name.endswith('.orig.rkt'))
    sys.exit(main(ns, a.apply))
