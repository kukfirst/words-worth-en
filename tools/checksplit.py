#!/usr/bin/env python3
"""Проверить, что разрез ничего не потерял: родитель зовёт ровно то, что спутник умеет.

Разрез -- единственная правка, которая меняет СТРУКТУРУ скрипта намеренно, поэтому
структурный гейт (`gates`, сверка скелета с `.orig.rkt`) на него неприменим. Остаётся
одно: пересчитать пару целиком.

Что ловится:

| проверка | что ловит |
|---|---|
| условия | ветку, вынесенную из родителя, но не доехавшую в спутника |
| вызов на месте | родителя, который на месте ветки зовёт не того спутника |
| нет сирот | спутника с веткой, которую родитель уже не зовёт, -- мёртвый текст |
| размеры | файл за `gates.MES_MAX` |
| имена | спутника, которого зовут, но которого нет в `en/` |

⚠️ Именно эта сверка поймала бы перезапись FLOOR05C 2026-09-14: у спутника осталась бы
одна ветка вместо семи, а родитель звал бы его в семи местах -- «нет сирот» наоборот.

    tools/checksplit.py            # все пары
    tools/checksplit.py FLOOR08.MES
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import gates                                                        # noqa: E402
import split                                                        # noqa: E402

EN = ROOT / 'en'


def norm(s):
    """Условие без оглядки на пробелы: сверяем смысл, а не форматирование."""
    return re.sub(r'\s+', ' ', s).strip()


def conds(src):
    """Условие каждой ветки диспетчера -> её тело. Без диспетчера -- пусто, не ошибка:
    большинство скриптов игры (бои, магазины, меню) никакого (cond ..) не содержат."""
    out = {}
    if '(cond' not in src:
        return out
    for br in split.branches(src):
        s, _ = br['span']
        cs, ce = split.sexprs(src, s + 1, br['span'][1] - 1)[0]
        out.setdefault(norm(src[cs:ce]), []).append(br['src'])
    return out


def check(name):
    """Список претензий к паре родитель/спутники. Пусто -- значит сошлось."""
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    bad = []
    parent = conds(src)

    # кого родитель зовёт и по какому условию
    wanted = {}
    for cond, bodies in parent.items():
        for body in bodies:
            for callee in re.findall(r'mes-call "([^"]+)"', body):
                wanted.setdefault(callee.upper().replace('.MES', '.MES'), set()).add(cond)

    for callee, need in sorted(wanted.items()):
        comp = EN / f'{callee}.rkt'
        if not comp.exists():
            bad.append(f'зовёт {callee}, а файла нет в en/')
            continue
        # родные спутники игры (у них есть японский оригинал) не наша забота:
        # их условия писал elf, и совпадать с родительскими они не обязаны
        if (EN / f'{callee}.orig.rkt').exists():
            continue
        have = set(conds(comp.read_text(encoding='utf-8')))
        for c in sorted(need - have):
            bad.append(f'{callee}: родитель зовёт по «{c[:56]}», а такой ветки в спутнике НЕТ')
        for c in sorted(have - need):
            bad.append(f'{callee}: ветка «{c[:56]}» есть, но родитель её уже не зовёт (мёртвый текст)')

    for f in [name] + sorted(wanted):
        mes = EN / f'{f}.rkt.mes'
        if not mes.exists():
            bad.append(f'{f}: не скомпилирован')
        elif mes.stat().st_size > gates.MES_MAX:
            bad.append(f'{f}: {mes.stat().st_size} б -- за порогом {gates.MES_MAX}')
    return bad, wanted


def main(names):
    total = 0
    for name in names:
        bad, wanted = check(name)
        ours = [c for c in wanted if not (EN / f'{c}.orig.rkt').exists()]
        if not ours and not bad:
            continue
        mark = '❌' if bad else '✅'
        print(f'{mark} {name:16} спутников наших {len(ours)}: {", ".join(sorted(ours)) or "—"}')
        for b in bad:
            print(f'      {b}')
        total += len(bad)
    print(f'\n{"❌ претензий: " + str(total) if total else "✅ все пары сошлись"}')
    return 1 if total else 0


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        args = sorted(p.name[:-4] for p in EN.glob('*.MES.rkt')
                      if not p.name.endswith('.orig.rkt'))
    sys.exit(main(args))
