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
    import terms
    rows = terms.scan()
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
                            str(ROOT / 'game/WordsWorth.hdi'), str(out), '--fresh'],
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
    base = (ROOT / 'game/WordsWorth.hdi').read_bytes()
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
    bad += run('патч и en/ сходятся', step_patch)
    bad += run('сборка играбельного образа', step_image)
    bad += run('патч на чужом расположении раздела', step_foreign)
    if screens:
        bad += run('экраны с кадров', step_screens)
    if probe:
        bad += run('запуск в эмуляторе', step_probe)
    print(f'\nпровалов: {bad}')
    sys.exit(bad)
