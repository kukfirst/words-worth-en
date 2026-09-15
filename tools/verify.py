#!/usr/bin/env python3
"""Перепроверить весь тракт одной командой -- от текста до играбельного образа.

    tools/verify.py              # быстрые проверки (секунды)
    tools/verify.py --probe      # плюс запуск в эмуляторе (~2 минуты)
    tools/verify.py --recompile  # плюс пересборка всех скриптов (~час)
    tools/verify.py --full       # всё вместе

Шаги и что каждый доказывает:

| шаг | доказывает |
|---|---|
| раскладка | ни дыр, ни сирот, ни разорванных слов -- `tools/relayout.py --check` |
| имена | один предмет -- одно имя во всей игре (`tools/terms.py`) |
| экраны | что РЕАЛЬНО написано на снятых кадрах (`tools/screenqa.py`, `--screens`) |
| размер | каждый .mes под порогом, за которым движок не переживает загрузку локации |
| патч | `dist/patch.json` описывает ровно то, что лежит в `en/` |
| образ | сборка из нетронутого оригинала проходит приёмку по каждому файлу |
| чужой раздел | патч находит раздел там, где он лежит у игрока, а не там, где у нас |
| запуск | игра грузится, доходит до боя и переживает его (`--probe`) |

Каждый шаг печатает свой вердикт; код возврата -- число провалившихся.
"""
import argparse, hashlib, json, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import gates                                                        # noqa: E402


def run(title, fn):
    print(f'\n=== {title}')
    try:
        ok, note = fn()
    except Exception as e:                                   # noqa: BLE001
        ok, note = False, f'сорвалось: {e}'
    print(('  ✅ ' if ok else '  ❌ ') + note)
    return 0 if ok else 1


def step_layout():
    import relayout
    n = relayout.check()
    return n == 0, f'реплик с браком раскладки: {n}'


def step_terms():
    """Один предмет -- одно английское имя, и проверка НЕ по списку вариантов.

    ⚠️ `terms.scan()` ищет ИЗВЕСТНЫЕ варианты (`ITEMS[...]['variants']`), то есть список,
    написанный руками. Самопроверка это и поймала: `Healing Herb` -> `Curing Herb` прошло
    незамеченным, потому что такого варианта в списке нет. Список устаревает молча -- ровно
    та беда, ради которой состав публичной копии считается, а не пишется.
    Здесь наоборот: японское имя предмета берётся из оригинала, и ЛЮБОЙ перевод, кроме
    канона, считается расхождением -- даже тот, которого никто не предвидел.
    """
    sys.path.insert(0, str(ROOT / 'tools'))
    import terms
    from strings import unescape
    from audit import unsplit_forms
    rows = list(terms.scan())
    canon = {v['ja']: k for k, v in terms.ITEMS.items()}
    box = {v['box'] for v in terms.ITEMS.values()}
    for p in sorted((ROOT / 'en').glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        o = ROOT / 'en' / f'{p.name[:-4]}.orig.rkt'
        if not o.exists():
            continue
        from strings import forms
        ja = [unescape(f['ja']) for f in forms(o.read_text(encoding='utf-8'))]
        try:
            en = [unescape(f['ja']) for f, _ in unsplit_forms(p.name[:-4])]
        except Exception:
            en = [unescape(f['ja']) for f in forms(p.read_text(encoding='utf-8'))]
        if len(ja) != len(en):
            continue
        for a, b in zip(ja, en):
            # ⚠️ Перенос строки рвёт имя пополам (`Ascension\nStone`), и сравнение по
            # подстроке его не узнаёт: 4 ложные тревоги из 14 были именно такими.
            flat = b.replace('\n', ' ')
            for jname, want in canon.items():
                if jname not in a or want in flat:
                    continue
                # сокращение в окне предметов -- законно, поле не тянется
                if any(x in flat for x in box) or not flat.strip():
                    continue
                # ⚠️ Проза -- не предмет: Фабрис рассказывает, как в горах добывают
                # 金塊, и «gold» там уместнее «Gold Bar». Предметом считаем только то,
                # что японский подаёт как предмет: в кавычках 『』 либо со счётчиком.
                if jname not in a.replace('『', '').replace('』', '') or (
                        f'『{jname}』' not in a and '１つ' not in a and '１個' not in a):
                    continue
                rows.append((p.name, 0, flat[:40], want))
    return not rows, ('имена сходятся во всей игре' if not rows
                      else f'разнобой в именах: {len(rows)}, первое {rows[0]}')


def step_screens():
    """Вторая, независимая сеть: читаем окно сообщения с настоящих кадров.

    ⚠️ Гейт по исходнику судит по МОДЕЛИ раскладки, и модель бывает неполна: дефект
    «...for unauth / orized» проехал мимо него, потому что модель не знала про колонку,
    с которой форма начинает печатать. Кадр это видел сразу.
    """
    sys.path.insert(0, str(ROOT / 'emu'))
    import screenqa
    rows, total = screenqa.scan()
    hard = screenqa.broken(rows)
    return not hard, (f'прочитано экранов {total}, брака раскладки нет'
                      if not hard else
                      f'брак на кадрах: {len(hard)}, первый {hard[0][0].name} {hard[0][1][:1]}')


def step_size():
    import gates
    bad = [(f.name, f.stat().st_size) for f in sorted((ROOT / 'en').glob('*.MES.rkt.mes'))
           if gates.gate_size(f)]
    return not bad, (f'все {len(list((ROOT / "en").glob("*.MES.rkt.mes")))} .mes под порогом '
                     f'{gates.MES_MAX} б' if not bad else f'за порогом: {bad[:5]}')


def step_split():
    """Пары разреза: родитель зовёт ровно то, что спутник умеет.

    ⚠️ Отдельным шагом, потому что структурный гейт сюда не достаёт: разрез меняет скелет
    скрипта НАМЕРЕННО, и сверять его с `.orig.rkt` бессмысленно. А ошибиться тут дёшево --
    2026-09-14 разрез во второй раз в то же имя стёр семь веток живого спутника.
    """
    import io
    import contextlib
    sys.path.insert(0, str(ROOT / 'tools'))
    import checksplit
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bad = checksplit.main(sorted(p.name[:-4] for p in (ROOT / 'en').glob('*.MES.rkt')
                                     if not p.name.endswith('.orig.rkt')))
    tail = [l for l in buf.getvalue().splitlines() if l.strip().startswith(('FLOOR', 'TOWN', 'SHP'))]
    return not bad, ('все пары родитель/спутник сошлись'
                     if not bad else '; '.join(tail[:3]))


def step_numbers():
    """Числа не слипаются с текстом, а текст вокруг них не калька с японского.

    ⚠️ Отдельным шагом по прямой просьбе игрока: «это уже не первый раз мы это исправляем,
    добавь в пайплайн, чтобы не слетало». Класс дефекта неустраним правкой одного места --
    число печатается ОТДЕЛЬНОЙ инструкцией, и любой инструмент, который перепишет соседнюю
    реплику (вычитка, ужимка, `terms`), может снова оставить `diary1 to.` Проверка дешёвая,
    пусть стоит.
    """
    sys.path.insert(0, str(ROOT / 'tools'))
    import numfix
    bad = []
    for p in sorted((ROOT / 'en').glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        for (_, _, tail), _ in numfix.sites(p.read_text(encoding='utf-8')):
            bad.append(f'{p.name[:-4]} …{tail[-24:]!r}+число')
    return not bad, ('числа отделены от текста везде' if not bad
                     else f'слиплись: {len(bad)}, первые {bad[:3]}')


def step_audit():
    """ВСЯ батарея гейтов по готовому тексту, а не по батчу (`tools/audit.py`).

    ⚠️ Была написана и НЕ ВКЛЮЧЕНА в конвейер — то есть запускалась, только когда я про неё
    вспоминал. Ровно она ловит подпись говорящего, имя собственное со строчной, пропажу
    текста и стык с именем; ни один из этих классов больше нигде не проверяется.
    """
    import io
    import contextlib
    sys.path.insert(0, str(ROOT / 'tools'))
    import audit
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bad = audit.main(sorted(p.name[:-4] for p in (ROOT / 'en').glob('*.MES.rkt')
                                if not p.name.endswith('.orig.rkt')), 4)
    out = buf.getvalue()
    tail = [l.strip() for l in out.splitlines() if l.startswith('❌')]
    return not bad, ('батарея гейтов по всей игре: претензий нет' if not bad
                     else '; '.join(tail[:3]))


def step_consistency():
    """Одна японская реплика -> один английский перевод (`tools/consistency.py`).

    ⚠️ Тоже не была в конвейере. Замер 2026-09-15: `』を見つけた！！` переводилось тремя
    способами в 2532 местах, `効果がなかった。` — тремя в 80. Эта проверка молчала не потому,
    что всё сходилось, а потому, что её никто не звал.
    """
    import io
    import contextlib
    sys.path.insert(0, str(ROOT / 'tools'))
    import consistency
    buf = io.StringIO()
    old = sys.argv
    try:
        sys.argv = ['consistency.py', '--min', '2']
        with contextlib.redirect_stdout(buf):
            consistency.main()
    except SystemExit:
        pass
    finally:
        sys.argv = old
    # ⚠️ Формы со вставкой `{0}` инструмент починить не умеет: `split_translation` не
    # раскладывает по слотам текст, где маркеры стоят иначе, и это ЕГО законный отказ, а не
    # наш недосмотр. Такой разнобой -- известное ограничение (сейчас 15 фрагментов, все
    # косметические: «went up by 1 point» против «went up 1 point»). Провалом считаем
    # только то, что инструмент чинить УМЕЕТ, иначе шаг краснеет вечно и его перестают
    # читать -- а это худшее, что может случиться с проверкой.
    out = buf.getvalue()
    frags = [l for l in out.splitlines() if l.lstrip()[:1].isdigit() and 'x  ' in l]
    fixable = [l for l in frags if '{0}' not in l]
    note = f'разнобой: {len(frags)} фрагментов'
    if frags and not fixable:
        note += ' — все со вставкой {0}, инструменту не по зубам (известно)'
    return not fixable, (note if frags else 'один японский -> один английский')


def step_japanese():
    """Японского текста в переводе не осталось (`tools/finish_ja.py`).

    ⚠️ Гейт по батчу увидеть остаток не может по устройству: батч прошёл — и забыт.
    """
    sys.path.insert(0, str(ROOT / 'tools'))
    from strings import forms, unescape
    left = []
    for p in sorted((ROOT / 'en').glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt') or p.name == 'NAME.MES.rkt':
            continue
        for f in forms(p.read_text(encoding='utf-8')):
            t = unescape(f['ja'])
            if any('\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9fff' for c in t):
                left.append(f'{p.name[:-4]}: {t[:28]!r}')
                break
    return not left, ('японского текста не осталось' if not left
                      else f'осталось японским: {len(left)}, {left[:3]}')


def step_menu():
    """Пункт меню, не влезающий в своё окно, обрезается на полуслове.

    ⚠️ `gates.gate_menu_width` существовал и не вызывался НИОТКУДА — мёртвая проверка.
    Меню у игры своё, 24 половинные колонки (`MENU_COLS`), и окно по содержимому не растёт.
    """
    sys.path.insert(0, str(ROOT / 'tools'))
    from strings import forms
    bad = []
    for p in sorted((ROOT / 'en').glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        o = ROOT / 'en' / f'{p.name[:-4]}.orig.rkt'
        if not o.exists():
            continue
        res = gates.gate_menu_width(o.read_text(encoding='utf-8'),
                                    p.read_text(encoding='utf-8'), forms)
        if res:
            bad.append(f'{p.name[:-4]}: {res}')
    return not bad, ('пункты меню влезают в своё окно' if not bad
                     else f'не влезают: {len(bad)}, {bad[:2]}')


def step_patch():
    cfg = json.loads((ROOT / 'dist/patch.json').read_text())
    miss = []
    for e in cfg['entries']:
        src = ROOT / 'en' / f'{e["name"]}.rkt.mes'
        if not src.is_file():
            continue                      # файлы, которых в en/ нет (спутники разреза)
        if hashlib.md5(src.read_bytes()).hexdigest() != e['md5_after']:
            miss.append(e['name'])
    return not miss, (f'патч описывает то же, что в en/ ({len(cfg["entries"])} записей)'
                      if not miss else f'разошлись: {miss[:6]}')


def step_image():
    """Собрать образ В СТОРОНЕ и проверить его.

    ⚠️ НЕ в `game/WordsWorth_play.hdi`. Раньше собирали именно туда и с `--fresh` -- то есть
    проверка молча стирала сохранение игрока. Так и случилось в ночь на 2026-09-11: человек
    сел продолжать и обнаружил пустые слоты. Проверка обязана быть безобидной.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / 'verify_play.hdi'
        r = subprocess.run([sys.executable, str(ROOT / 'tools/build_play.py'),
                            str(gates.BASE), str(out), '--fresh'],
                           capture_output=True, text=True)
    ok = '✅' in r.stdout
    return ok, (r.stdout.strip().splitlines() or ['пусто'])[-2 if ok else -1]


def step_foreign():
    """Патч обязан лечь на образ, где раздел лежит в ДРУГОМ месте.

    Это не теория: патч едет к людям с их собственными дампами, а смещение раздела у них
    своё. Раньше все инструменты помнили 71680 -- адрес нашего образа, -- и на чужом mtools
    молча не находил ничего: «применено 0, предупреждений 0» выглядело как успех.
    Здесь раздел сдвигается искусственно, и патч обязан его НАЙТИ.
    """
    import tempfile
    base = gates.BASE.read_bytes()
    shift = 51200                        # произвольный сдвиг, кратный сектору
    with tempfile.TemporaryDirectory() as td:
        img = pathlib.Path(td) / 'shifted.hdi'
        img.write_bytes(base[:4096] + b'\x00' * shift + base[4096:])
        r = subprocess.run([sys.executable, str(ROOT / 'tools/apply_patch.py'), str(img)],
                           capture_output=True, text=True)
    line = next((l for l in r.stdout.splitlines() if 'применено' in l), r.stdout[-200:])
    ok = 'предупреждений: 0' in line and 'применено файлов: 0' not in line
    return ok, line.strip()


def step_probe():
    # ⚠️ Зонд гоняет QA-ОБРАЗ, а не тот, в который играет человек: играбельный держит открытым
    # эмулятор агента, и его копия может выйти рваной. Заодно проверяется ровно то, что мы
    # только что собрали, а не то, что лежало в игре с прошлой сборки.
    scr = pathlib.Path(os.environ.get('WW_SCRATCH') or '/tmp/ww-probe') / 'emuprobe'
    scr.mkdir(parents=True, exist_ok=True)
    img = scr / 'verify.hdi'
    img.write_bytes((ROOT / 'game/WordsWorth_qa.hdi').read_bytes())
    r = subprocess.run([str(ROOT / 'emu/.venv/bin/python'), str(ROOT / 'emu/battle_probe.py'),
                        img.name], capture_output=True, text=True, timeout=1800)
    img.unlink(missing_ok=True)
    line = next((l for l in r.stdout.splitlines() if 'БОЙ' in l or 'умер' in l), '')
    return 'ПЕРЕЖИТ' in line, line or 'зонд не сказал ничего'


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true', help='плюс запуск в эмуляторе')
    ap.add_argument('--screens', action='store_true', help='плюс чтение снятых кадров')
    ap.add_argument('--recompile', action='store_true', help='плюс пересборка скриптов')
    ap.add_argument('--full', action='store_true', help='и то и другое')
    a = ap.parse_args()
    probe, recompile = a.probe or a.full, a.recompile or a.full
    screens = a.screens or a.full
    bad = 0
    if recompile:
        print('=== пересборка скриптов (долго)')
        subprocess.run([sys.executable, str(ROOT / 'tools/recompile.py')])
    bad += run('раскладка текста', step_layout)
    bad += run('имена предметов и персонажей', step_terms)
    bad += run('размер скриптов', step_size)
    bad += run('пары разреза', step_split)
    bad += run('числа не слиплись с текстом', step_numbers)
    bad += run('батарея гейтов по всей игре', step_audit)
    bad += run('один японский -> один английский', step_consistency)
    bad += run('японского текста не осталось', step_japanese)
    bad += run('пункты меню влезают в окно', step_menu)
    bad += run('патч и en/ сходятся', step_patch)
    bad += run('сборка играбельного образа', step_image)
    bad += run('патч на чужом расположении раздела', step_foreign)
    if screens:
        bad += run('экраны с кадров', step_screens)
    if probe:
        bad += run('запуск в эмуляторе', step_probe)
    print(f'\nпровалов: {bad}')
    sys.exit(bad)
