#!/usr/bin/env python3
"""Translation units for juice .rkt sources.

A unit is a WHOLE (text ...) form, not a single string literal. A form may hold
several string slots separated by runtime insertions -- `(text "[" 0 "]: hi")`
is the hero's name spliced into a line. Translating the slots separately hands
the model fragments like `さんか。` (an honorific with nothing to attach to),
so the slots are joined into one sentence with {0}-style markers standing in
for the insertions, and split apart again on the way back.

Byte spans are recorded per slot, so re-insertion never rematches text.
"""
import json, pathlib, re, sys

_MARK = re.compile(r'\{(\d+)\}')


def _elements(src, open_paren):
    """Split one (text ...) form into elements. Returns (elements, end_index).

    Each element is ('str', start, end, value) or ('raw', text).
    """
    i = open_paren + 1
    n = len(src)
    depth = 1
    els = []
    buf = []
    while i < n:
        c = src[i]
        if c == '"':
            j = i + 1
            while j < n:
                if src[j] == '\\':
                    j += 2
                    continue
                if src[j] == '"':
                    break
                j += 1
            if buf:
                els.append(('raw', ''.join(buf)))
                buf = []
            els.append(('str', i + 1, j, src[i+1:j]))
            i = j + 1
            continue
        if c == ';':
            k = src.find('\n', i)
            i = n if k < 0 else k + 1
            continue
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                if buf:
                    els.append(('raw', ''.join(buf)))
                return els, i
        buf.append(c)
        i += 1
    return els, n


def forms(src):
    """Yield one dict per (text ...) form that contains at least one string.

    ⚠️ An insertion BEFORE the first string (or after the last) is part of the line
    too: `(text 0 "は『石板のかけら』を手に入れた！！")` prints the hero's name and then
    the sentence, with nothing between them. Trimming it -- which this did until
    2026-09-08 -- handed the model a subject-less predicate, it translated a whole
    sentence, and the game rendered «AstralYou got a Stone Tablet Fragment!!».
    Measured blast radius at the time: 345 lines. So edge insertions get a {N}
    marker like any other, and `lead`/`trail` say the marker sits at an edge (there
    is no string slot there, so `split_translation` has to drop the empty piece).
    """
    out = []
    for m in re.finditer(r'\(text\b', src):
        els, end = _elements(src, src.index('(', m.start()))
        if not any(e[0] == 'str' for e in els):
            continue
        body = list(els)
        if body and body[0][0] == 'raw':          # strip the `text` keyword itself
            head = re.sub(r'^\s*text\b', '', body[0][1])
            body[0] = ('raw', head)
        slots, parts, ins, k = [], [], [], 0
        for e in body:
            if e[0] == 'str':
                slots.append((e[1], e[2]))
                parts.append(e[3])
            else:
                txt = e[1].strip()
                if not txt:
                    continue
                parts.append('{%d}' % k)
                ins.append(txt)
                k += 1
        first_str = next(i for i, x in enumerate(parts) if not _MARK.fullmatch(x))
        last_str = len(parts) - 1 - next(i for i, x in enumerate(reversed(parts))
                                         if not _MARK.fullmatch(x))
        out.append({'start': m.start(), 'end': end + 1,
                    'slots': slots, 'ins': ins, 'ja': ''.join(parts),
                    'lead': first_str, 'trail': len(parts) - 1 - last_str})
    return out


def split_translation(en, nslots, nins, lead=0, trail=0):
    """Split a translated line back into per-slot pieces. None if markers are wrong.

    `lead`/`trail` count markers that sit outside every string slot (see `forms`):
    the model must still write them, but they leave an empty piece with no span to
    write it to, and anything non-empty there would be text the game never shows.
    """
    found = _MARK.findall(en)
    if len(found) != nins or [int(x) for x in found] != list(range(nins)):
        return None
    pieces = _MARK.split(en)
    # re.split with a group gives [text, num, text, num, text...]
    texts = pieces[0::2]
    for _ in range(lead):
        if not texts or texts[0].strip():
            return None
        texts = texts[1:]
    for _ in range(trail):
        if not texts or texts[-1].strip():
            return None
        texts = texts[:-1]
    return texts if len(texts) == nslots else None


def rebuild_dict(src):
    """Swap the literal (dict …) for (dict-build).

    The original dictionary holds Japanese glyphs, so English text falls back to
    two-byte SJIS and the file doubles in size (END3: 30 297 vs 15 200 bytes).
    Regenerating it from the translated text brought that to 19 642. Note the
    form spans lines and the first '(dict' in the file is actually (dictbase N).
    """
    m = re.search(r'\(dict\s+#', src)
    if not m:
        return src
    i = m.start()
    depth = 0
    for j in range(i, len(src)):
        if src[j] == '(':
            depth += 1
        elif src[j] == ')':
            depth -= 1
            if depth == 0:
                return src[:i] + '(dict-build)' + src[j+1:]
    return src


def unescape(s):
    """Снять экранирование исходника: на экране \\" -- это одна кавычка, а не две.

    ⚠️ forms() отдаёт СЫРОЙ текст литерала, вместе с обратными слэшами, а patch()
    экранирует заново. Скормить ему уже экранированный текст -- значит получить \\\\"
    вместо \\" и показать игроку лишние слэши: ровно это я и сделал в первой версии,
    испортив 9 реплик в 8 файлах. Плюс счёт колонок врал: \\" считалось за два знака.

    ⚠️ `\\n` -- ПЕРЕВОД СТРОКИ, а не буква `n`. juice пишет разрывы именно так, и
    tools/relayout.py ставит их сам; наивное «снять слэш» превращало бы разрыв в букву
    посреди слова при каждом перечитывании.
    """
    out, i = [], 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            out.append('\n' if s[i + 1] == 'n' else s[i + 1]); i += 2
        else:
            out.append(s[i]); i += 1
    return ''.join(out)


def escape(s):
    """Обратное к unescape: текст -> тело строкового литерала."""
    return (s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n'))


def patch(src, edits):
    """edits: (start, end, new_value) over string-literal spans, applied right-to-left."""
    for start, end, val in sorted(edits, key=lambda e: -e[0]):
        src = src[:start] + val + src[end:]
    return src


# kept for callers that only want raw literals
def scan(src):
    return [(s, e, src[s:e]) for f in forms(src) for (s, e) in f['slots']]


if __name__ == '__main__':
    if sys.argv[1] == 'dump':
        units = []
        for p in sorted(pathlib.Path(sys.argv[2]).glob('*.MES.rkt')):
            src = p.read_text(encoding='utf-8')
            for f in forms(src):
                units.append({'file': p.name, 'slots': f['slots'],
                              'ins': f['ins'], 'ja': f['ja'],
                              'lead': f['lead'], 'trail': f['trail']})
        json.dump(units, open(sys.argv[3], 'w'), ensure_ascii=False, indent=0)
        multi = sum(1 for u in units if len(u['slots']) > 1)
        print(f'{len(units)} units ({multi} with a name insertion) '
              f'from {len(set(u["file"] for u in units))} files -> {sys.argv[3]}')

# --- цифры в окне сообщения -----------------------------------------------------------
# Движок печатает (number …) двухбайтовыми кодами, и в половинном шрифте английской сборки
# они выходят половинками глифов («dealt :( damage!!»). Замер (tools/number_probe.py, один
# бой, урон 10) развёл три гипотезы: счётчик цифр @20[8:11] даёт «(:(» — он задаёт только
# ширину поля; полноширинный шрифт даёт «１０» — читаемо, но широко; а бит 12 @20 даёт
# ровно «10». Панель статуса ставит его же — потому там цифры всегда были целы.
# Вставка канонична дословно: gates.skeleton() вырезает эти две строки С ОБЕИХ сторон,
# иначе structure-гейт справедливо ругался бы на инструкцию, которой нет в оригинале.
NUM_ON = '(set-arr~ @ 20 (// (&& (~ @ 20) 4095) 4096))'
NUM_OFF = '(set-arr~ @ 20 (&& (~ @ 20) 4095))'
# ⚠️ Шаблон требует СЛАГАЕМОГО: (+ (&& (~ @ 20) 61695) N) -- именно так игра выставляет
# счётчик цифр панели. Один раз я расширил его до любой маски 61695 и снял 337 обёрток,
# решив, что они валят игру: после снятия вылет сдвинулся дальше. Это была корреляция,
# а не причина -- игру валила заливка пробелами до края строки (§12 STATUS). Расширение
# выкосило рабочую правку, и цифры в бою снова стали половинками: «dealt :( damage».
_COUNT = re.compile(r'\(set-arr~ @ 20 \(\+ \(&& \(~ @ 20\) 61695\) \d+\)\)')


def number_fix(src):
    """Обернуть каждую форму (text …) с (number …) в «половинные цифры включены».

    ⚠️ Только числа В ПРЕДЛОЖЕНИИ. Панель статуса, список предметов и прайс лавки рисуют
    числа сами: ставят курсор (@17) и счётчик цифр (@20 биты 8-11) — туда лезть нельзя.
    Замер: правка ОДНОГО START.MES, девять вставок из тринадцати в отрисовке панели, и
    игра мертва на загрузке SENTO00 (emu/battle_probe.py, 35-й шаг, воспроизводится).
    Признак такого места — запись счётчика поблизости перед формой; плюс голое
    (text (number …)) без единой буквы — это всегда панель, никогда не фраза.
    """
    spots, last = [], 0
    for m in re.finditer(r'\(text\b', src):
        op = src.index('(', m.start())
        if op < last:
            continue
        els, end = _elements(src, op)
        if not any(e[0] == 'raw' and '(number' in e[1] for e in els):
            continue
        last = end + 1
        if not any(e[0] == 'str' and any(c.isalpha() for c in e[3]) for e in els):
            continue                       # голые цифры — панель, не фраза
        if _COUNT.search(src[max(0, op - 400):op]):
            continue                       # рядом выставлен счётчик цифр — тоже панель
        # ⚠️ идемпотентность: recompile.py правит en/*.rkt НА МЕСТЕ, и без этой проверки
        # второй прогон складывает обёртки одна на другую (проверено: (set-arr~ @ 20 …)
        # дважды подряд, скелет расходится на лишний разделитель).
        if src[max(0, op - len(NUM_ON) - 4):op].rstrip().endswith(NUM_ON):
            continue
        spots.append((op, end + 1))
    for a, b in reversed(spots):
        src = src[:a] + NUM_ON + ' ' + src[a:b] + ' ' + NUM_OFF + src[b:]
    return src, len(spots)

