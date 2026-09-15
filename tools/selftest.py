#!/usr/bin/env python3
"""Проверить ПРОВЕРКИ: сломать по одному дефекту каждого класса и убедиться, что заорут.

## Зачем

Проверок накопилось девять, и каждая написана после того, как соответствующий дефект уже
доехал до игрока. Молчаливая проверка неотличима от исправного текста, поэтому «всё
зелёное» ничего не значит, пока не доказано, что зелёный цвет вообще умеет меняться.

Здесь на КОПИИ каталога `en/` по очереди портится по одной вещи, и для каждой сверяется,
что её ловит именно тот шаг, который для неё написан. Не поймала — это провал теста, а не
мелочь: значит класс дефектов снова стал невидимым.

⚠️ Рабочий `en/` не трогается: всё происходит во временном каталоге, а инструменты
натравливаются на него через `WW_EN`... которого у них нет. Поэтому проще: каталог
подменяется на время прогона и возвращается в `finally` -- и это единственное место, где
такая подмена допустима.

    tools/selftest.py            # все классы
    tools/selftest.py числа      # один
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
EN = ROOT / 'en'


def find(pat, files='*.MES.rkt', skip_orig=True):
    """Первый файл, где встречается образец."""
    for p in sorted(EN.glob(files)):
        if skip_orig and p.name.endswith('.orig.rkt'):
            continue
        s = p.read_text(encoding='utf-8')
        m = re.search(pat, s)
        if m:
            return p, s, m
    return None, None, None


# (имя класса, как сломать, чем ловится, что должно прозвучать)
def break_speaker():
    p, s, m = find(r'"\[[A-Z][a-z]+\]: ')
    p.write_text(s[:m.start()] + '"' + s[m.end():], encoding='utf-8')
    return f'{p.name}: срезана подпись говорящего'


def break_lowercase_name():
    p, s, m = find(r'"\[Club\]:')
    p.write_text(s[:m.start()] + '"[club]:' + s[m.end():], encoding='utf-8')
    return f'{p.name}: имя с маленькой буквы'


def break_number():
    p, s, m = find(r'"HP restored by "')
    p.write_text(s[:m.start()] + '"HP restored by"' + s[m.end():], encoding='utf-8')
    return f'{p.name}: число приклеено к тексту'


def break_charset():
    p, s, m = find(r'"\[[A-Z][a-z]+\]: [A-Z]')
    p.write_text(s[:m.end()] + 'Ж' + s[m.end():], encoding='utf-8')
    return f'{p.name}: знак вне кодировки игры'


def break_layout():
    """Слово, которое не влезает и порвётся."""
    p, s, m = find(r'"\[[A-Z][a-z]+\]: [A-Za-z ,\.]{30,}"')
    long = '"' + m.group(0)[1:-1] + ' ' + 'x' * 40 + '"'
    p.write_text(s[:m.start()] + long + s[m.end():], encoding='utf-8')
    return f'{p.name}: строка не влезает в окно'


def break_split():
    """Удалить ветку у спутника: родитель зовёт то, чего больше нет."""
    import split
    for p in sorted(EN.glob('*C.MES.rkt')):
        if (EN / f'{p.name[:-4]}.orig.rkt').exists():
            continue
        s = p.read_text(encoding='utf-8')
        brs = split.branches(s)
        if not brs:
            continue
        a, e = brs[0]['span']
        p.write_text(s[:a] + s[e:], encoding='utf-8')
        return f'{p.name}: у спутника удалена ветка'
    return None


def break_size():
    """Файл за порогом. Портим СКОМПИЛИРОВАННЫЙ .mes: `step_size` смотрит именно на него,
    а пересборка ради теста стоила бы двадцати минут."""
    import gates
    p = EN / 'FLOOR00.MES.rkt.mes'
    p.write_bytes(p.read_bytes() + b'\0' * (gates.MES_MAX + 100 - p.stat().st_size))
    return f'{p.name}: раздут за порог {gates.MES_MAX}'


def break_terms():
    """Один предмет назван по-другому — разнобой во всей игре.

    ⚠️ Место ищется ТЕМ ЖЕ способом, что и проверкой (`audit.unsplit_forms`): предмет в
    кавычках 『消炎草』 встречается только в разрезанных и боевых файлах, где ряды форм с
    оригиналом не совпадают. Прямое сравнение `forms()` таких мест не находит вовсе, и
    первая версия теста объявляла проверку слепой, хотя слеп был тест.
    """
    from strings import forms, unescape
    from audit import unsplit_forms
    for q in sorted(EN.glob('*.MES.rkt')):
        if q.name.endswith('.orig.rkt'):
            continue
        o = EN / f'{q.name[:-4]}.orig.rkt'
        if not o.exists():
            continue
        ja = [unescape(f['ja']) for f in forms(o.read_text(encoding='utf-8'))]
        try:
            pairs = unsplit_forms(q.name[:-4])
        except Exception:
            continue
        if len(pairs) != len(ja):
            continue
        for a, (f, owner) in zip(ja, pairs):
            if '『消炎草』' not in a:
                continue
            t = unescape(f['ja'])
            if 'Healing Herb' not in t:
                continue
            # ⚠️ Портим ИМЕННО ЭТУ форму, по её слотам. Бить по первому вхождению в файле
            # нельзя: оно оказывается в другой форме, где японский подаёт вещь прозой, и
            # проверка законно молчит -- а тест объявляет её слепой. Ломать надо там, куда
            # смотрит проверка.
            fp = EN / owner
            src = fp.read_text(encoding='utf-8')
            a0, b0 = f['slots'][0]
            piece = src[a0:b0].replace('Healing Herb', 'Curing Herb')
            if piece == src[a0:b0]:
                continue                      # нечего ломать -- молчать об этом нельзя
            fp.write_text(src[:a0] + piece + src[b0:], encoding='utf-8')
            return f'{owner}: предмет переименован в форме {src[a0:b0][:30]!r}'
    return None


def break_text_loss():
    """Пропажа текста: у спутника стёрта реплика."""
    for p in sorted(EN.glob('FLOOR0*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        s = p.read_text(encoding='utf-8')
        m = re.search(r'\(text "[A-Za-z][^"]{30,}"\)', s)
        if m:
            p.write_text(s[:m.start()] + '(text "")' + s[m.end():], encoding='utf-8')
            return f'{p.name}: реплика опустошена'
    return None


CASES = {
    'подпись':   (break_speaker,       'audit', 'подпись говорящего'),
    'имя':       (break_lowercase_name, 'audit', 'имя с маленькой буквы'),
    'числа':     (break_number,        'verify:step_numbers', 'слиплись'),
    'кодировка': (break_charset,       'audit', 'кодировка'),
    'раскладка': (break_layout,        'verify:step_layout', 'браком раскладки: '),
    'разрез':    (break_split,         'verify:step_split', 'НЕТ'),
    'размер':    (break_size,          'verify:step_size', 'за порогом'),
    'имена':     (break_terms,         'verify:step_terms', 'разнобой'),
    'пропажа':   (break_text_loss,     'audit', 'опустело'),
}


def run_check(which):
    """Запустить проверку и вернуть её вывод."""
    if which == 'audit':
        r = subprocess.run([sys.executable, str(ROOT / 'tools/audit.py'), '--show', '3'],
                           capture_output=True, text=True, timeout=3600)
        return r.stdout
    step = which.split(':')[1]
    code = (f'import sys; sys.path.insert(0, {str(ROOT / "tools")!r}); import verify; '
            f'ok, note = verify.{step}(); print(("OK " if ok else "FAIL ") + str(note))')
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                       timeout=3600, cwd=ROOT)
    return r.stdout + r.stderr


def main(only):
    backup = pathlib.Path(tempfile.mkdtemp(prefix='selftest-en.'))
    shutil.copytree(EN, backup / 'en')
    print(f'копия рабочего каталога: {backup}\n')
    good = bad = 0
    try:
        for name, (breaker, checker, expect) in CASES.items():
            if only and name != only:
                continue
            shutil.rmtree(EN)
            shutil.copytree(backup / 'en', EN)
            what = breaker()
            if what is None:
                print(f'  {name:11} ⚠️ нечего ломать — класс не представлен')
                continue
            out = run_check(checker)
            caught = expect in out and 'OK ' not in out.split('\n')[0]
            # для audit признак другой: претензия названа
            if checker == 'audit':
                caught = expect in out
            print(f'  {name:11} {"✅ поймано" if caught else "❌ ПРОПУЩЕНО"}  ({checker})')
            print(f'              сломано: {what}')
            if not caught:
                bad += 1
                for l in out.strip().splitlines()[-4:]:
                    print(f'              {l[:96]}')
            else:
                good += 1
    finally:
        shutil.rmtree(EN, ignore_errors=True)
        shutil.copytree(backup / 'en', EN)
        shutil.rmtree(backup, ignore_errors=True)
        print('\nрабочий каталог восстановлен')
    print(f'\n{"✅ все проверки ловят свой класс" if not bad else f"❌ слепых проверок: {bad}"}'
          f'  (проверено {good + bad})')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
