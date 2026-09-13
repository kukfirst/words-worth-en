#!/usr/bin/env python3
"""Mechanical gates. None of these ask the model anything."""
import os, re, subprocess, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from strings import scan

# ⚠️ juice НЕ вендорится: у него нет лицензии (проверено по API GitHub -- `license: None`),
# то есть права на распространение нам никто не давал. Он клонируется в `tools/juice`
# скриптом `tools/get_juice.sh` на закреплённом коммите. Путь ищется рядом с этим файлом,
# а не в домашнем каталоге автора; переопределяется переменной `WW_JUICE`.
JUICE = pathlib.Path(os.environ.get('WW_JUICE') or
                     pathlib.Path(__file__).resolve().parent / 'juice/mes/juice.rkt')
# The allowed set is whatever charset "english" actually maps -- read it from
# juice rather than assuming printable ASCII. Notably it has no backslash and no
# tilde, and a stray "\" makes the compiled file unparseable (SENTO0A).
_CHARSET = pathlib.Path(__file__).resolve().parent / 'juice/mes/charset/_charset_english.rkt'
try:
    ALLOWED = {ord(c) for c in re.findall(r'#\\(.)', _CHARSET.read_text(encoding='utf-8'))}
    ALLOWED |= {0x20}
except OSError:
    ALLOWED = set(range(0x20, 0x7f)) | {0xa5, 0xaf}

def gate_count(ja_list, en_list):
    if len(ja_list) != len(en_list):
        return f'count mismatch: {len(ja_list)} in, {len(en_list)} out'
    return None

def gate_emptied(ja_list, en_list):
    """A line that had content must keep content.

    Whitespace-only lines (　　　　 -- ideographic spaces used as layout) came
    back empty, and juice then dies compiling them with 'max: arity mismatch'.
    """
    bad = [i for i, (a, b) in enumerate(zip(ja_list, en_list)) if a and not b]
    return f'emptied lines: {bad[:6]}' if bad else None


def gate_charset(en_list):
    bad = []
    for i, s in enumerate(en_list):
        for ch in s:
            if ord(ch) not in ALLOWED:
                bad.append((i, ch))
                break
    return f'non-charset chars: {bad[:5]}' if bad else None

def gate_glossary(ja_list, en_list, terms):
    """Lock a term only where it is actually a name.

    Katakana proper nouns (Fabrice, Sharon) are names wherever they appear.
    Kanji speaker labels are different: 剣士 is "Swordsman" as a dialogue label
    ［剣士］：, but plain "swordsman" inside a sentence -- demanding the glossary
    form everywhere rejects perfectly good lines (measured on YADO.MES).
    """
    miss = []
    for i, (ja, en) in enumerate(zip(ja_list, en_list)):
        for k, v in terms.items():
            katakana = bool(re.fullmatch(r'[ァ-ヶー]+', k))
            present = (k in ja) if katakana else (f'［{k}］' in ja)
            if present and v.lower() not in en.lower():
                miss.append((i, k, v))
    return f'glossary violations: {miss[:5]}' if miss else None


def _screen_width(t):
    """Half-width cells. Japanese glyphs take two, charset-english ASCII takes one."""
    return sum(1 if ord(c) < 0x2000 else 2 for c in t)


def gate_width(ja_list, en_list, wrap):
    """The engine word-wraps, so a long line is fine -- what matters is that the
    translation does not need noticeably MORE box than the original did, and that
    no single word is too long to wrap."""
    bad = []
    for i, (ja, en) in enumerate(zip(ja_list, en_list)):
        budget = _screen_width(ja) * 1.25 + 24
        if _screen_width(en) > budget:
            bad.append((i, _screen_width(en), int(budget)))
            continue
        longest = max((len(w) for w in en.split()), default=0)
        if longest > wrap:
            bad.append((i, f'unbreakable word {longest}>{wrap}'))
    return f'too wide for the original box: {bad[:4]}' if bad else None


_SKEL_STR = re.compile(r'"(?:[^"\\]|\\.)*"')
# only meta here -- the dictionary is removed later by paren balance, and this
# regex would bite off just its first line and leave the glyphs behind
_SKEL_DROP = re.compile(r'^\s*\(meta\b.*?\)\s*$', re.M | re.S)
_SKEL_FONT = re.compile(r'\(set-arr~ @ 21 (?:\([^()]*\)|[^()])*\)')
# ⚠️ Слепое пятно по делу: английская сборка ставит бит 12 @20 перед каждым
# (number …), иначе цифры рисуются половинками глифов (strings.number_fix).
# Вырезаем ровно эти две записи, и С ОБЕИХ сторон — сравнение остаётся честным.
def _drop_numfix(s):
    """Вырезать записи @20 про бит 12 -- регуляркой не выйдет, там три уровня скобок."""
    out, i = [], 0
    while True:
        j = s.find('(set-arr~ @ 20', i)
        if j < 0:
            out.append(s[i:]); break
        d, k = 0, j
        while k < len(s):
            d += (s[k] == '(') - (s[k] == ')')
            if d == 0:
                break
            k += 1
        form = s[j:k+1]
        if '4095' in form or '4096' in form:
            # съедаем пробел и слева тоже, иначе на месте вырезанной формы остаётся
            # лишний разделитель и скелеты расходятся ровно на него
            out.append(s[i:j].rstrip(' \n\t'))
            i = k + 1
            while i < len(s) and s[i] in ' \n\t':
                i += 1
            # разделитель нужен ровно там, где дальше идёт следующая инструкция, а не ')'
            if i < len(s) and s[i] != ')':
                out.append(' ')
        else:
            out.append(s[i:k+1]); i = k + 1
    return ''.join(out)

def skeleton(src: str) -> str:
    """Instruction skeleton: text contents blanked, dict/meta and font-width normalised."""
    s = re.sub(r'\(text-raw[^)]*\)', '(text "")', src)
    s = _SKEL_STR.sub('""', s)
    s = _SKEL_DROP.sub('', s)
    s = _SKEL_FONT.sub('(set-arr~ @ 21 *)', s)
    s = _drop_numfix(s)
    s = re.sub(r'\s+', ' ', s).strip()   # переносы — не структура
    # the dict differs between original and translation by design; drop it whole
    while True:
        m = re.search(r'\(dict(?:-build)?[\s)]', s)
        if not m:
            break
        depth, i, j = 0, m.start(), m.start()
        while j < len(s):
            if s.startswith('#\\', j):     # a character literal: #\( and #\) are data
                j += 3
                continue
            if s[j] == '(':
                depth += 1
            elif s[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        s = s[:i] + s[j+1:]
    s = re.sub(r'\s+', ' ', s).strip()
    # How a line is chopped into text instructions is not preserved when its content
    # changes -- one (text ...) may compile into two, or a trailing empty one may
    # appear. That is not a logic change. So collapse each RUN of consecutive text
    # instructions into a single canonical token that keeps only the runtime
    # insertions (the `1`/`0` procs spliced into the line) and their order.
    # Everything structural -- waits, conditions, jumps, set-reg, proc calls outside
    # text, instruction order -- still has to match exactly.
    def _run(m):
        # Count runtime insertions across the whole run, wherever they sit. The
        # engine's word-wrap splits a long line into several text instructions at
        # compile time, so an insertion can land mid-run or even lead an
        # instruction: (text "…was\n") (text 1 " without the beard."). What must
        # match is which insertions the block prints, and in what order.
        ins = []
        for body, proc in re.findall(r'\(text([^)]*)\)|\(proc (\d+)\)', m.group(0)):
            if proc:
                ins.append(proc)
            else:
                ins += re.findall(r'(?<![\w"])(\d+)(?![\w"])', body)
        return '(TEXT ' + ' '.join(ins) + ') '
    # note \s* not ' ' -- a text instruction can be the last thing before a
    # closing paren, and requiring a trailing space left it out of the run
    run = r'\(text(?: ""| \d+)*\)\s*'
    s = re.sub(rf'(?:{run})+(?:\(proc \d+\)\s*(?:{run})+)*', _run, s)
    return s



# Измерено на FLOOR05.MES (emu/size_ladder.py, emu/bisect_crash.py): 40 092 б переживает
# загрузку, 40 115 б убивает игру. Подтверждено дважды на непересекающихся наборах строк —
# префиксами и суффиксами, — то есть дело в РАЗМЕРЕ, а не в конкретной строке.
# START.MES (11 104 б) резидентен всегда и грузится по 0x95f0, сценарий сцены встаёт ровно за
# ним по 0xC150 — база одна и та же у всех сценариев, поэтому потолок общий.
# Самый большой ОРИГИНАЛЬНЫЙ скрипт — 36 958 б, так что японские файлы влезают с запасом.
MES_MAX = 40_000        # запас ~90 б до измеренной границы


def gate_size(mes_path):
    """Скомпилированный сценарий не должен превышать буфер движка.

    Это не косметика: за порогом игра не «подтормаживает», а умирает при заходе в локацию,
    и ещё ДО смерти затирает соседний блок данных игрока (str/def приходят мусором).
    """
    n = mes_path.stat().st_size
    if n > MES_MAX:
        return (f'compiled {n} b > {MES_MAX} b: движок не переживёт загрузку этой локации '
                f'(измеренная граница 40 092/40 115)')
    return None

def juice(args, cwd):
    return subprocess.run(['racket', str(JUICE)] + args, cwd=cwd,
                          capture_output=True, text=True, timeout=900)

def _literalised(sk: str) -> str:
    """Нормализовать скелет так, чтобы «имя литералом вместо словарного текста» не считалось
    расхождением: TEXT и str — один и тот же вывод строки, а формы вывода строки можно
    снимать и добавлять.

    ⚠️ ЧТО ЭТО ПЕРЕСТАЁТ ПРОВЕРЯТЬ: у файла из LITERALISED больше не сверяется ни выбор
    строкового опкода, ни само наличие форм вывода строки. Порядок веток, вызовы, процедуры
    и работа с регистрами сверяются как прежде — а именно там живут поломки, которые гейт
    и заводился ловить. Взамен снятого: emu/item_probe.py снимает бокс названия кадром,
    emu/save_probe.py проверяет, что резидентный скрипт жив.
    """
    s = sk.replace('TEXT', 'str')
    s = re.sub(r'\s*""', '', s)                       # содержимое строк skeleton() уже обнулил
    s = re.sub(r'\((?:str|text)\s*\)\s*', '', s)      # пустая форма вывода строки
    return s


# Поле названия предмета не разжимает словарь .MES, поэтому английские названия пишутся
# литералом (str …), а не (text …) — иначе на экране половинки кандзи. Замеры и разбор:
# STATUS.md §13; правку делает tools/itembox.py, кадром проверяет emu/item_probe.py.
#
# ⚠️ Список не перечисляем руками: блок есть в 28 файлах (START, START1 и 26 боевых
# SENTO*), и захардкоженный набор разъехался бы с ними при первом же изменении. Признак —
# сам блок в переведённом исходнике.
_ITEMBOX = '((== (~ @ 23) 0)'


def literalised(workdir: pathlib.Path, name: str) -> bool:
    src = workdir / f'{name}.rkt'
    return src.exists() and _ITEMBOX in src.read_text(encoding='utf-8')


def gate_compile_and_structure(workdir: pathlib.Path, name: str):
    """name like FLOOR01.MES. Requires <name>.rkt (translated) and <name>.orig.rkt."""
    r = juice(['-cf', f'{name}.rkt'], workdir)
    mes = workdir / f'{name}.rkt.mes'
    if not mes.exists() or mes.stat().st_size == 0:
        return f'compile failed: {r.stderr.strip()[:300] or r.stdout.strip()[:300]}'
    tmp = workdir / 'roundtrip'
    tmp.mkdir(exist_ok=True)
    (tmp / name).write_bytes(mes.read_bytes())
    # must mirror how work/*.rkt were produced: --protag 0,1 (NOT -p ww, which
    # forces protag 0 and un-fuses name insertions), and english charset for
    # translated text, else juice emits text-raw.
    r = juice(['-df', '--protag', '0,1', '--charset', 'english', name], tmp)
    back = tmp / f'{name}.rkt'
    if not back.exists():
        return f'recompiled file will not decompile: {r.stderr.strip()[:200]}'
    # Объявленные исправления логики (tools/logicfix.py) применяются и к ЭТАЛОНУ: тогда
    # разрешено ровно объявленное изменение, а любое другое по-прежнему ловится.
    import logicfix
    a = skeleton(logicfix.fixed(name, (workdir / f'{name}.orig.rkt').read_text(encoding='utf-8')))
    b = skeleton(back.read_text(encoding='utf-8'))
    if a != b and literalised(workdir, name):
        a, b = _literalised(a), _literalised(b)
    if a != b:
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return f'structure diverges at char {i}: orig …{a[max(0,i-60):i+60]}… new …{b[max(0,i-60):i+60]}…'
        return f'structure length differs: {len(a)} vs {len(b)}'
    return gate_size(mes)

_MARK = re.compile(r'\{(\d+)\}')
_ATTACH = 'はがをにへともで、'      # particles that bind to whatever was printed just before


def gate_edges(ja_list, en_list):
    """A name or number the engine prints hard against the line must not weld to a word.

    Two shapes, one failure. `(text 0 "は『石板のかけら』…")` carries the insertion inside the
    form, so the English carries a {0} -- but the marker being present proves nothing: what
    matters is the character right AFTER it, because that is what the name butts against.
    When the name comes from a preceding proc there is no marker at all and the only clue
    is the particle the Japanese opens with; then the English must open with a space
    (or 's, or a comma).

    ⚠️ Report, not a hard gate: a menu item legitimately opens with は ("はい" -> "Yes") and
    the name-entry screen is a kana grid. Callers filter those (tools/repair_edges.py).
    """
    bad = []
    for i, (ja, en) in enumerate(zip(ja_list, en_list)):
        ja, en = ja or '', en or ''
        if not ja.strip() or not en.strip() or '\u3000' in ja:
            continue
        if ja.lstrip().startswith('{'):
            if not en.lstrip().startswith('{'):
                bad.append((i, ja[:48], en[:48]))
                continue
            after = _MARK.sub('', en.lstrip(), count=1)
            if after and (after[0].isalnum() or after[0] == '"'):
                bad.append((i, ja[:48], en[:48]))
        elif ja[0] in _ATTACH and len(ja) > 3 and (en[0].isalnum() or en[0] == '"'):
            bad.append((i, ja[:48], en[:48]))
    return bad

MENU_COLS = 24     # ширина строки меню, снята с самого движка: proc 13 рисует
                   # (box-inv 37 … 60 …) и (box 1 24 24 39) -- 24 половинные колонки,
                   # окно фиксированное и по содержимому НЕ растягивается.


def menu_spans(src):
    """Байтовые границы каждого (menu-show …): пункт внутри -- это строка меню."""
    out = []
    for m in re.finditer(r'\(menu-show\b', src):
        d, i = 0, m.start()
        while i < len(src):
            if src[i] == '(':
                d += 1
            elif src[i] == ')':
                d -= 1
                if d == 0:
                    break
            i += 1
        out.append((m.start(), i))
    return out


def _cols(t):
    """Ширина в половинных колонках: японский знак занимает две, латиница одну."""
    return sum(2 if ord(c) > 0x2000 else 1 for c in t)


def gate_menu_width(orig_src, en_src, forms_fn):
    """Пункт меню, который не влезает в окно, обрезается на полуслове.

    Замечено человеком на экране: «What's the Swordsman's P» -- окно кончилось. Меню
    рисует proc 13 с фиксированной шириной 24, поэтому проверка точная, а не на глаз.
    Меню, где сам ОРИГИНАЛ шире 24, пропускаем: такое окно рисует другой код.
    """
    sp = menu_spans(orig_src)
    fo, fe = forms_fn(orig_src), forms_fn(en_src)
    if len(fo) != len(fe):
        return []
    bad = []
    for a, b in zip(fo, fe):
        span = next((x for x in sp if x[0] <= a['start'] <= x[1]), None)
        if not span:
            continue
        if any(_cols(f['ja']) > MENU_COLS for f in fo
               if span[0] <= f['start'] <= span[1]):
            continue
        w = _cols(b['ja'])
        if w > MENU_COLS:
            bad.append((w, a['ja'][:24], b['ja'][:48]))
    return bad

