#!/usr/bin/env python3
"""Разрезать комнату за порогом на два скрипта: родитель + спутник.

Порог 40 000 б -- это буфер движка, и он на ФАЙЛ, а не на комнату. Игра сама этим
пользуется: FLOOR08 зовёт floor08a.mes пятнадцать раз, а вместе они весят 72 819 б.
Приём (списан с готовой пары FLOOR08/FLOOR08A):

  родитель:  ((&& (== V 6) ...) (<> (mes-call "floor08b.mes")))
  спутник:   ((&& (== V 6) ...) (<> ...настоящее тело...))

Спутник ПЕРЕПРОВЕРЯЕТ то же условие -- значит V и флаги (: NNN) глобальны и переход
переживают. Проверено на живой паре: FLOOR08A тестирует ровно то условие, по которому
FLOOR08 его вызвал.

⚠️⚠️ СПУТНИК ПЕРЕЗАПИСЫВАЕТСЯ ЦЕЛИКОМ, а не дополняется. `build()` собирает его с нуля из
выбранных сейчас веток, и `--apply` кладёт результат поверх `--into`. Значит ВТОРОЙ разрез
того же файла в ТОТ ЖЕ спутник сотрёт ветки, вынесенные первым разрезом, — а родитель
продолжит их звать, и эти события умрут. Ловушка поймана 2026-09-14 до записи: у `FLOOR02B`
было 7 веток и 7 072 б, предлагаемый спутник — 1 458 б. Второй разрез делать ТОЛЬКО в новое
имя (`FLOOR02C.MES` и далее).

⚠️ Спутник НЕ определяет процедур: ни FLOOR08A, ни FLOOR09A не содержат define-proc,
хотя зовут (proc 10). Определения родителя переживают вызов. Поэтому спутнику нужна
только преамбула (meta/dict-build/slot/slot) и диспетчер с (break) в конце -- «отработал
один раз и вернулся». У родителя цикл вечный, у спутника -- однократный.

    tools/split.py FLOOR08.MES --into FLOOR08B.MES          # померить
    tools/split.py FLOOR08.MES --into FLOOR08B.MES --apply  # записать
"""
import argparse, pathlib, re, shutil, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import gates

ROOT = pathlib.Path(__file__).resolve().parent.parent
EN = ROOT / 'en'
LIMIT = 40000


def close(s, i):
    """Индекс закрывающей скобки для открывающей на i. Строки и ; -- не скобки."""
    d, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == '"':
            i += 1
            while i < n and s[i] != '"':
                i += 2 if s[i] == '\\' else 1
        elif c == ';':
            i = s.find('\n', i)
            if i < 0:
                return n
        elif c == '(':
            d += 1
        elif c == ')':
            d -= 1
            if d == 0:
                return i
        i += 1
    return n


def sexprs(s, i, end):
    """Спаны s-выражений верхнего уровня в срезе [i, end)."""
    out = []
    while i < end:
        if s[i] == '(':
            e = close(s, i)
            out.append((i, e + 1))
            i = e + 1
        else:
            i += 1
    return out


def main_cond(src):
    """Спан самого крупного (cond ..) -- это диспетчер действий игрока."""
    spans = [(m.start(), close(src, m.start())) for m in re.finditer(r'\(cond\b', src)]
    return max(spans, key=lambda p: p[1] - p[0])


def branches(src):
    """Ветки диспетчера, кроме (else ..), с разбором на условие и тело."""
    a, b = main_cond(src)
    out = []
    for s, e in sexprs(src, a + len('(cond'), b):
        body = src[s:e]
        if body.startswith('(else'):
            continue
        cs, ce = sexprs(src, s + 1, e - 1)[0]        # первое s-выражение -- условие
        out.append({'span': (s, e), 'cond_end': ce, 'src': body,
                    'text': sum(len(x) for x in re.findall(r'"((?:[^"\\]|\\.)*)"', body)),
                    'nested': bool(re.search(r'mes-call|mes-jump', body)),
                    'label': re.sub(r'\s+', ' ', src[cs:ce])[:60]})
    return out


def preamble(src):
    """Всё до первой инструкции верхнего уровня, не входящей в шапку спутника."""
    keep = ('(meta', '(dict-build', '(slot', '(set-arr~', '(field')
    i, out = src.index('(mes') + len('(mes'), []
    for s, e in sexprs(src, i, len(src)):
        if not src[s:e].startswith(keep):
            break
        out.append(src[s:e])
        if src[s:e].startswith('(field'):
            break
    return out


def build(parent_src, picked, callee):
    """(новый родитель, исходник спутника)."""
    call = f'(<> (mes-call "{callee.lower()}"))'
    out, last = [], 0
    for br in sorted(picked, key=lambda b: b['span'][0]):
        s, e = br['span']
        out.append(parent_src[last:br['cond_end']])
        out.append(' ' + call + ')')
        last = e
    out.append(parent_src[last:])
    parent = ''.join(out)

    head = '\n '.join(preamble(parent_src))
    bodies = '\n    '.join(br['src'] for br in sorted(picked, key=lambda b: b['span'][0]))
    comp = (f'(mes\n {head}\n (while\n  (== 1 1)\n  (<>\n   (proc 10)\n   (cond\n    '
            f'{bodies}\n    (else (<>)))\n   (break))))\n')
    return parent, comp


def size_of(src, name):
    """Скомпилированный размер. Ответ даёт juice, не арифметика."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='split.'))
    (tmp / f'{name}.rkt').write_text(src, encoding='utf-8')
    gates.juice(['-cf', f'{name}.rkt'], tmp)
    mes = tmp / f'{name}.rkt.mes'
    n = mes.stat().st_size if mes.exists() else 0
    shutil.rmtree(tmp, ignore_errors=True)
    return n


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument('name')
    ap.add_argument('--into', required=True, help='имя файла-спутника, напр. FLOOR08B.MES')
    ap.add_argument('--margin', type=int, default=2000,
                    help='запас под порогом: 57 б -- не запас, измеренная граница 40 092')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    src = (EN / f'{args.name}.rkt').read_text(encoding='utf-8')
    size0 = (EN / f'{args.name}.rkt.mes').stat().st_size
    brs = branches(src)
    print(f'{args.name}: {size0} б, сверх порога {size0 - LIMIT} б; веток {len(brs)}')

    # берём самые тяжёлые, пока родитель не влезет -- размер спрашиваем у компилятора
    # ⚠️ Ветку с mes-call/mes-jump выносить НЕЛЬЗЯ: она стала бы вложенным вызовом из
    # спутника, а этого в игре нет ни разу -- проверены все девять спутников (FLOOR08A,
    # 09A, 10A, 10B, 00A, 01A, 02A, 03A, 05B): mes-call 0, mes-jump 0. Опираться на
    # глубину вложения, которую движок нигде не демонстрирует, -- гадание.
    movable = [b for b in brs if not b['nested']]
    print(f'  выносимых (без вложенных вызовов): {len(movable)} из {len(brs)}, '
          f'в них {sum(b["text"] for b in movable)} знаков текста', flush=True)
    # ⚠️ Запас -- ЦЕЛЬ, а не требование: весь оставшийся текст сидит в ветках с
    # собственными вызовами, глубже жать нечем. Берём лучшее достижимое и
    # останавливаемся, когда вынос перестал помогать -- пустая ветка весит меньше,
    # чем ставящийся на её место (mes-call ..), и родитель растёт: измерено 30 веток
    # -> 39 172 б, 31-я -> 39 182, 35-я -> 39 222.
    picked, best, stall = [], None, 0
    for br in sorted(movable, key=lambda b: -b['text']):
        picked.append(br)
        parent, comp = build(src, picked, args.into)
        pn, cn = size_of(parent, args.name), size_of(comp, args.into)
        print(f'  вынесено {len(picked):2d}: {br["text"]:5d} знаков  родитель {pn} б, '
              f'спутник {cn} б  {br["label"]}', flush=True)
        if best is None or pn < best[0]:
            best, stall = (pn, cn, list(picked), parent, comp), 0
        else:
            stall += 1
        if 0 < pn <= LIMIT - args.margin and 0 < cn <= LIMIT:
            break
        if stall >= 2:
            print('  вынос перестал уменьшать родителя -- останавливаюсь', flush=True)
            break
    pn, cn, picked, parent, comp = best
    if not (0 < pn <= LIMIT and 0 < cn <= LIMIT):
        print(f'\nне сошлось: лучшее -- родитель {pn} б, спутник {cn} б, порог {LIMIT}')
        return
    if pn > LIMIT - args.margin:
        print(f'\n⚠️ запас {LIMIT - pn} б вместо заданных {args.margin}: остальной текст '
              f'сидит в ветках с вызовами, выносить их нельзя')
    print(f'\nлучшее: вынесено {len(picked)} веток')

    tmp = pathlib.Path(tempfile.mkdtemp(prefix='splitgate.'))
    for nm, s in ((args.name, parent), (args.into, comp)):
        (tmp / f'{nm}.rkt').write_text(s, encoding='utf-8')
    shutil.copyfile(EN / f'{args.name}.orig.rkt', tmp / f'{args.name}.orig.rkt')
    g = gates.juice(['-cf', f'{args.name}.rkt'], tmp), gates.juice(['-cf', f'{args.into}.rkt'], tmp)
    ok = all((tmp / f'{n}.rkt.mes').exists() for n in (args.name, args.into))
    print(f'\nкомпиляция обоих: {"ok" if ok else "СБОЙ"}')
    print(f'{size0} -> родитель {pn} б + спутник {cn} б (порог {LIMIT} на файл)')
    # ⚠️ Гейт структуры здесь неприменим: он сверяет скелет с .orig.rkt, а мы структуру
    # меняем НАМЕРЕННО. Единственная настоящая проверка -- зайти в комнату в эмуляторе.
    if args.apply and ok:
        (EN / f'{args.name}.rkt').write_text(parent, encoding='utf-8')
        (EN / f'{args.into}.rkt').write_text(comp, encoding='utf-8')
        shutil.copyfile(tmp / f'{args.name}.rkt.mes', EN / f'{args.name}.rkt.mes')
        shutil.copyfile(tmp / f'{args.into}.rkt.mes', EN / f'{args.into}.rkt.mes')
        print(f'\nзаписано: en/{args.name}.rkt и en/{args.into}.rkt (+ .mes)')
        print('⚠️ проверить в эмуляторе: зайти в комнату и вызвать вынесенные ветки')
    else:
        print(f'\nНЕ ЗАПИСАНО. Применить: tools/split.py {args.name} --into {args.into} --apply')
    shutil.rmtree(tmp, ignore_errors=True)




def companions(name):
    """Спутники, которых родитель зовёт и которых В ИГРЕ НЕ БЫЛО -- то есть наши.

    Отличаем по отсутствию японского оригинала: floor08a.mes родной (у него есть
    FLOOR08A.MES.orig.rkt), floor08b.mes создан разрезом.
    """
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    out = []
    for m in re.finditer(r'\(mes-call "([a-z0-9_]+\.mes)"\)', src):
        nm = m.group(1).upper()[:-4] + '.MES'
        if not (EN / f'{nm}.orig.rkt').exists() and (EN / f'{nm}.rkt').exists():
            out.append(nm)
    return sorted(set(out))


def unsplit(name):
    """Исходник, каким он был бы без разреза: тела веток возвращены из спутников.

    ⚠️ Зачем. Гейт структуры сверяет скелет с японским оригиналом, а разрез структуру
    меняет НАМЕРЕННО -- после разреза девяти комнат recompile дал ok=84 bad=9, и все
    девять «провалов» это разрезанные родители. Списать их на «так и задумано» нельзя:
    этот гейт -- единственное, что доказывает, что скрипт не покорёжен, и отключить его
    там, где мы больше всего наменяли, значит остаться без страховки. Поэтому сверяем
    реконструкцию. Побочная выгода: совпадение скелета доказывает, что разрез не потерял
    и не переставил ни одной ветки.
    """
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    bodies = {}
    for c in companions(name):
        for br in branches((EN / f'{c}.rkt').read_text(encoding='utf-8')):
            cs, ce = sexprs(br['src'], 1, len(br['src']) - 1)[0]
            key = re.sub(r'\s+', ' ', br['src'][cs:ce])
            bodies[key] = br['src'][ce:-1]
    out, last, restored = [], 0, 0
    for br in branches(src):
        s, e = br['span']
        tail = src[br['cond_end']:e - 1]
        if 'mes-call' not in tail:
            continue
        cs, ce = sexprs(br['src'], 1, len(br['src']) - 1)[0]
        key = re.sub(r'\s+', ' ', br['src'][cs:ce])
        if key not in bodies:
            continue
        out.append(src[last:br['cond_end']])
        out.append(bodies[key])
        last = e - 1
        restored += 1
    out.append(src[last:])
    return ''.join(out), restored


if __name__ == '__main__':     # ⚠️ иначе импорт ради unsplit() запускает разбор argv
    run()


def gate_split_parent(name):
    """Гейт для разрезанного родителя. None -- всё честно.

    Цепочка из двух звеньев, вместе равная обычному gate_compile_and_structure:
      1. реконструкция (родитель + спутники) совпадает по скелету с японским оригиналом
         -- значит разрез не потерял и не переставил ни одной ветки;
      2. родитель компилируется и разбирается обратно в себя же -- значит компилятор
         ничего не покорёжил.
    Первое звено заменяет сверку с оригиналом, которая после разреза невозможна;
    второе сохраняет проверку компиляции, которая от разреза не зависит.
    """
    rec, k = unsplit(name)
    if not k:
        return f'{name}: реконструкция не вернула ни одной ветки -- спутники не найдены'
    orig = (EN / f'{name}.orig.rkt').read_text(encoding='utf-8')
    if gates.skeleton(orig) != gates.skeleton(rec):
        a, b = gates.skeleton(orig), gates.skeleton(rec)
        i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        return f'реконструкция расходится с оригиналом на {i}: …{a[max(0,i-40):i+40]}…'

    tmp = pathlib.Path(tempfile.mkdtemp(prefix='splitgate.'))
    try:
        shutil.copyfile(EN / f'{name}.rkt', tmp / f'{name}.rkt')
        r = gates.juice(['-cf', f'{name}.rkt'], tmp)
        mes = tmp / f'{name}.rkt.mes'
        if not mes.exists() or not mes.stat().st_size:
            return f'compile failed: {(r.stderr or r.stdout).strip()[:200]}'
        back = tmp / 'roundtrip'
        back.mkdir(exist_ok=True)
        (back / name).write_bytes(mes.read_bytes())
        gates.juice(['-df', '--protag', '0,1', '--charset', 'english', name], back)
        got = back / f'{name}.rkt'
        if not got.exists():
            return 'recompiled file will not decompile'
        if gates.skeleton(got.read_text(encoding='utf-8')) != gates.skeleton(
                (EN / f'{name}.rkt').read_text(encoding='utf-8')):
            return 'компиляция не обратима: разобранный файл не совпал с исходником'
        return gates.gate_size(mes)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
