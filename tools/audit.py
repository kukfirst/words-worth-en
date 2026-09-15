#!/usr/bin/env python3
"""Прогнать ВСЮ батарею проверок по готовому переводу целиком, а не по батчу.

## Зачем это отдельно от `translate.py`

При переводе гейты судили КАЖДЫЙ БАТЧ -- и это правда. Но после перевода текст правили ещё
четыре инструмента: `tighten.py` (срезал хвосты ради размера), `shopfix.py` (переписал
денежные строки), `finish_ja.py` (добил японские остатки), `import_text.py` (вернул вычитку).
Ни один из них не зовёт батарею целиком. Значит утверждение «всё проверено» опирается на
состояние, которого больше нет.

Плюс новая дорога -- вычитка -- по определению не может звать гейты, которым нужен японский
оригинал: она его не видит. Тем нужнее прогон, который его видит.

Здесь японское берётся из `en/*.MES.orig.rkt`, английское из `en/*.MES.rkt`, и по ним
гоняется всё, что у нас есть, включая то, чего нет ни на одной дороге:

| проверка | откуда |
|---|---|
| charset, emptied, glossary, width, edges | `gates` -- те же, что при переводе |
| подпись говорящего | здесь; японская `［имя］：` обязана дать английскую `[Name]: ` |
| раскладка в окне | `render.flaws` с учётом колонки старта (`column`) |
| размер | `gates.gate_size` |

⚠️ Только читает. Ничего не чинит и не пишет.

    tools/audit.py                # вся игра
    tools/audit.py FLOOR05.MES    # один файл
    tools/audit.py --show 20      # больше примеров на каждую претензию
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

# ⚠️ Файлы, которые НАРОЧНО остаются японскими, и почему. Не «шум, который надоел», а
# разобранные случаи: экран ввода имени -- это сетка каны, игрок набирает имя японскими
# знаками, и переводить её нечего. Раскладка там задаётся координатами курсора, а не
# переносом строк, поэтому модель окна к ней неприменима по устройству.
JAPANESE_BY_DESIGN = {'NAME.MES'}
# Японская подпись говорящего: ［剣士］： -- полноширинные скобки и двоеточие.
JA_SPEAKER = re.compile(r'^\s*[［\[][^］\]]*[］\]]\s*[：:]')
EN_SPEAKER = re.compile(r'^\s*\[[^\]]*\]\s*:')


def family(name):
    """Родитель и наши спутники: вместе они несут ВЕСЬ текст исходного файла.

    ⚠️ После разреза сверять форму с формой по номеру нельзя: часть веток уехала в спутника,
    и у родителя форм меньше (FLOOR05: 553 -> 435). Так аудит терял 39 файлов из 84 -- то
    есть половину текста игры, ровно ту, где сюжет. Поэтому счётные проверки идут по семье
    целиком: выравнивание по номеру для них не нужно, нужна полнота.
    """
    out = [name]
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    for c in sorted({c.upper() for c in re.findall(r'mes-call "([^"]+)"', src)}):
        if (EN / f'{c}.rkt').exists() and not (EN / f'{c}.orig.rkt').exists():
            out.append(c)
    return out


_LIT = re.compile(r'"((?:[^"\\]|\\.)*)"')


def literals(path):
    """Строковые литералы с содержимым -- мера полноты текста, устойчивая к перенарезке.

    ⚠️ Считать ФОРМЫ для этого нельзя, и это измерено: `itembox.py` переписал меню предметов
    литералами вместо словаря, и счётчик форм показал «START.MES: 27 -> 0» и «у каждого
    SENTO пропало 5» -- 28 ложных тревог на ровном месте, при том что весь текст на месте
    (101 английское название предметов в START.MES, проверено перечислением). Литерал же
    виден в любом виде: 189 против 189 у START.MES, 421 против 421 у SENTO02.
    """
    s = pathlib.Path(path).read_text(encoding='utf-8')
    return [x for x in _LIT.findall(s) if x.strip() and x.strip('　 ')]


def labelled(texts):
    """Сколько реплик несут подпись говорящего."""
    return sum(1 for t in texts if EN_SPEAKER.match(t) or JA_SPEAKER.match(t))


def speaker_census(name):
    """Подписи по СЕМЬЕ против оригинала. Ответ: (в японском, в английском)."""
    ja = [unescape(f['ja']) for f in forms((EN / f'{name}.orig.rkt').read_text(encoding='utf-8'))]
    en = []
    for m in family(name):
        en += [unescape(f['ja']) for f in forms((EN / f'{m}.rkt').read_text(encoding='utf-8'))]
    return labelled(ja), labelled(en), len(ja), len(en)


def unsplit(name):
    """Английский исходник в порядке ОРИГИНАЛА: на место `(mes-call …)` возвращается ветка.

    ⚠️ Без этого аудит сверял 39 файлов из 84 только счётчиками — а это все разрезанные
    этажи, то есть половина текста игры и весь сюжет. Разрез не меняет текст, он переносит
    ветки; значит операция обратима, и после неё формы снова выравниваются с японскими
    один в один (проверено: FLOOR02 604=604, FLOOR05 553=553, FLOOR08 649=649).
    """
    src = (EN / f'{name}.rkt').read_text(encoding='utf-8')
    comp = {}
    for c in {c.upper() for c in re.findall(r'mes-call "([^"]+)"', src)}:
        p = EN / f'{c}.rkt'
        if not p.exists() or (EN / f'{c}.orig.rkt').exists():
            continue                       # родной спутник игры — не наш разрез
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
    """Формы в порядке ОРИГИНАЛА, но каждая со СВОИМ файлом и настоящими смещениями.

    ⚠️ `unsplit()` склеивает текст и тем самым ломает смещения: писать по ним нельзя (это
    уже покорёжило перевод однажды). Здесь порядок восстанавливается так же, но форма несёт
    имя файла, где она лежит НА САМОМ ДЕЛЕ, — значит правку можно записать по месту.

    Возвращает [(форма, имя файла)].
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
            # формы ВНУТРИ ветки спутника, со смещениями в файле спутника
            inner = [f for f in forms(cs)
                     if br['span'][0] <= f['start'] < br['span'][1]]
            comp[cond] = (f'{c}.rkt', inner)
    # ⚠️ Идти по ФОРМАМ нельзя: в ветке с `(mes-call …)` форм не осталось вовсе — текст
    # уехал в спутника, — и такая ветка для обхода по формам невидима. Поэтому идём по
    # СОБЫТИЯМ: формы родителя и ветки-вызовы, отсортированные по смещению.
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
    """(японская форма, английская форма, колонка старта) по порядковому номеру."""
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
        return ('РАЗНОЕ ЧИСЛО ФОРМ', len(ja), len(en))
    return ja, en, c0


def check(name, terms):
    ja, en, c0 = pairs(name)
    bad = {}

    def note(kind, what):
        bad.setdefault(kind, []).append(what)

    if name in JAPANESE_BY_DESIGN:
        return bad                      # см. JAPANESE_BY_DESIGN
    for g, res in (('кодировка', gates.gate_charset(en)),
                   ('опустело', gates.gate_emptied(ja, en)),
                   ('глоссарий', gates.gate_glossary(ja, en, terms)),
                   ('ширина', gates.gate_width(ja, en, 56))):
        if res:
            note(g, res if isinstance(res, str) else str(res))

    # ⚠️ `gate_edges` -- отчёт, а не гейт, и сам это говорит: пункт меню законно начинается
    # с `は` («はい» -> «Yes»). Замер 2026-09-15: все 4 его претензии оказались пунктами
    # МЕНЮ, где японское начинается с は/も не как частица, а как первый слог слова
    # («はっきり» -- «honestly», «もちろん» -- «of course»). Отсекаем меню границами,
    # которые даёт сама игра (`gates.menu_spans`), а не списком слов.
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
        note('стык с именем', f'#{row[0]} {row[-1]!r}')

    for i, (a, b, c) in enumerate(zip(ja, en, c0)):
        if not a.strip() or not b.strip():
            continue
        # ⚠️ Подпись говорящего. Гейта на неё не было НИ НА ОДНОЙ дороге: старой она была не
        # нужна (модель переводила строку вместе с подписью), а новая японского не видит.
        # Обнаружено 2026-09-15, когда вычитка срезала `[Kaiser]: ` у пяти реплик подряд.
        if bool(JA_SPEAKER.match(a)) != bool(EN_SPEAKER.match(b)):
            note('подпись говорящего', f'#{i} ja={a[:24]!r} en={b[:34]!r}')
        # ⚠️ Имя собственное с МАЛЕНЬКОЙ буквы. Катакана в японской подписи -- это имя
        # (［クラブ］), и английское обязано начинаться с заглавной. Поймано игроком:
        # `［クラブ］` давало `[Club]` 69 раз и `[club]` 29 раз, а слово «club» -- ещё и
        # дубина, поэтому на глаз это не ловится и глоссарий молчал (его там нет).
        # ⚠️ Перенос ВНУТРИ подписи рвёт имя говорящего пополам (`[Armor Shop\nOwner]:`),
        # а ведущий перенос даёт пустую строку перед репликой. Появляется не при переводе,
        # а при ПЕРЕКЛАДКЕ раскладки, поэтому ни один гейт перевода этого не видит.
        # Найдено 2026-09-15 шагом `consistency`, когда он наконец попал в конвейер.
        # ⚠️ Смотрим ТОЛЬКО между скобками: `EN_SPEAKER` захватывает и ведущий перенос,
        # и проверка ловила собственное лечение как дефект (поймано сразу же).
        head = re.match(r'\s*\[([^\]]*)\]', b)
        if head and '\n' in head.group(1):
            note('перенос внутри подписи', f'#{i} {b[:34]!r}')
        # ⚠️ Ведущий перенос перед подписью -- НЕ дефект, а лечение: когда форма печатается
        # с колонки 39, имя говорящего в остаток строки не влезает, и его надо начать с
        # новой. Сначала я счёл это браком и убрал -- раскладка тут же покраснела на тех же
        # пяти репликах. Убирать надо перенос ВНУТРИ подписи, заменяя его на ведущий.
        ja_tag, en_tag = JA_SPEAKER.match(a), EN_SPEAKER.match(b)
        if ja_tag and en_tag:
            inner_ja = ja_tag.group(0).strip('［[］]：: ')
            inner_en = en_tag.group(0).strip('[]: ')
            if (re.fullmatch(r'[ァ-ヶー・]+', inner_ja) and inner_en
                    and inner_en[0].isalpha() and inner_en[0].islower()):
                note('имя с маленькой буквы', f'#{i} ja=［{inner_ja}］ en=[{inner_en}]')
        # ⚠️ Форма, начинающаяся с подстановки имени, -- известное исключение: перенос
        # положить НЕКУДА, первая строка целиком занята чужим именем. `verify.py` считает
        # их отдельной строкой («плюс N форм, начинающихся с подстановки») ровно поэтому.
        if b.lstrip().startswith('{'):
            continue
        why = flaws(screen(parts_of(b), w=None, col0=c), col0=c)
        if why:
            note('раскладка', f'#{i} {sorted(set(why))} {b[:34]!r}')

    mes = EN / f'{name}.rkt.mes'
    if mes.exists() and gates.gate_size(mes):
        note('размер', f'{mes.stat().st_size} б за порогом {gates.MES_MAX}')
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
        # Разрезанный файл: по номерам не сверить, но семью можно пересчитать целиком.
        if isinstance(p, tuple) and p and p[0] == 'РАЗНОЕ ЧИСЛО ФОРМ':
            sj, se, nj, ne = speaker_census(name)
            # Полнота -- по ЛИТЕРАЛАМ, а не по формам (см. literals(): формы дают 28 ложных).
            lj = len(literals(EN / f'{name}.orig.rkt'))
            le = sum(len(literals(EN / f'{m}.rkt')) for m in family(name))
            # ⚠️ Рост -- норма и не тревога: разрез даёт спутнику собственную преамбулу со
            # СЛОВАРЁМ СЖАТИЯ (`dict-build`), а это литералы, плюс имя файла в `mes-call`.
            # Замер: FLOOR08 1084 -> 1117. Тревога -- только УБЫЛЬ.
            if le < lj:
                totals['текст потерян'] = totals.get('текст потерян', 0) + (lj - le)
                examples.setdefault('текст потерян', []).append(
                    f'{name}: литералов {lj} -> {le} по семье {"+".join(family(name))}')
            if sj != se:
                totals['подписей потеряно'] = totals.get('подписей потеряно', 0) + abs(sj - se)
                examples.setdefault('подписей потеряно', []).append(
                    f'{name}: подписей {sj} -> {se}')
            continue
        for kind, items in check(name, terms).items():
            totals[kind] = totals.get(kind, 0) + len(items)
            examples.setdefault(kind, []).extend(f'{name} {x}' for x in items)

    print(f'проверено файлов: {len(names) - len(skipped)}'
          f'{f", без японского оригинала (наши спутники): {len(skipped)}" if skipped else ""}')
    if not totals:
        print('\n✅ претензий нет')
        return 0
    print()
    for kind, n in sorted(totals.items(), key=lambda x: -x[1]):
        print(f'❌ {kind}: {n}')
        for x in examples[kind][:show]:
            print(f'     {x}')
    print(f'\nвсего претензий: {sum(totals.values())}')
    return 1


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--show', type=int, default=6)
    a = ap.parse_args()
    ns = a.names or sorted(p.name[:-4] for p in EN.glob('*.MES.rkt')
                           if not p.name.endswith('.orig.rkt'))
    sys.exit(main(ns, a.show))
