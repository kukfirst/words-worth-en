#!/usr/bin/env python3
"""Собрать играбельный образ: исходный HDI + патч перевода -> новый файл.

    tools/build_play.py [исходный.hdi] [итоговый.hdi] [--fresh]

По умолчанию `game/WordsWorth.hdi` -> `game/WordsWorth_play.hdi` -- тот самый образ,
на котором играет человек (`emu/agent.py` в режиме «я», `WW_DISK`).

Исходник НЕ трогаем: работаем на копии и переименовываем её в итоговый файл только после
проверки. Оборвётся посередине -- предыдущий играбельный образ останется целым.

## Сохранение переживает пересборку

Патч переписывает `FLAG0..FLAG4` -- пять слотов сохранения. Не по прихоти: там лежат имена
героев, которые движок подставляет в реплики (`tools/savenames.py`). Но взять слот из патча
готовым значит убить прогресс игрока при каждой новой сборке, а ради этого всё и делается:
нашёл дефект -- я чиню -- ты продолжаешь с того же места.

Поэтому слоты идут особым путём: они ВЫНИМАЮТСЯ из прежнего играбельного образа и
кладутся в новый как есть, а латиницей переписываются только два поля имён внутри них.
`--fresh` -- начать с чистых слотов (прогресс будет потерян).

⚠️ Чистый слот берётся из ОРИГИНАЛА (`FLAG1..FLAG4` там побайтно одинаковы -- это
нетронутый шаблон), а НЕ из патча. В патче слот 0 несёт чужой сейв: он приехал вместе с
самим образом из коллекции (68 б отличий от пустого) -- кто-то поиграл до нас. Ставить его
игроку в «ロード1» незачем.

⚠️ Пересобирать образ, открытый работающим эмулятором, нельзя -- скрипт откажется.
"""
import functools, hashlib, json, pathlib, shutil, subprocess, sys, tempfile, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hdimage
from savenames import latinise, is_latin, NAME_SLOTS

# печать без буфера: иначе строки родителя выезжают после вывода apply_patch
print = functools.partial(print, flush=True)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist'
SLOTS = [f'FLAG{i}' for i in range(5)]


def md5(p):
    return hashlib.md5(pathlib.Path(p).read_bytes()).hexdigest()


def mtenv(img):
    d = pathlib.Path(tempfile.mkdtemp(prefix='wwmt.'))
    cfg = d / 'mtoolsrc'
    hdimage.mtoolsrc(img, cfg)
    return os.environ | {'MTOOLSRC': str(cfg), 'MTOOLS_SKIP_CHECK': '1'}, d


def read_slots(img):
    """Достать пять слотов сохранения из образа: имя -> байты."""
    env, d = mtenv(img)
    got = {}
    try:
        for name in SLOTS:
            f = d / name
            subprocess.run(['mcopy', '-o', f'z:/WW/{name}', str(f)], env=env, capture_output=True)
            if f.is_file():
                got[name] = f.read_bytes()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return got


def empty_slot(img):
    """Нетронутый шаблон слота из образа.

    В оригинале FLAG1..FLAG4 побайтно одинаковы -- это и есть «сохранения нет».
    Если вдруг разошлись, образ не тот, за какой себя выдаёт, и молчать об этом нельзя.
    """
    got = read_slots(img)
    blanks = {got[n] for n in SLOTS[1:] if n in got}
    if len(blanks) != 1:
        sys.exit(f'в {img.name} слоты FLAG1..FLAG4 не одинаковы -- где пустой, не понять')
    return blanks.pop()


def write_slots(img, slots):
    env, d = mtenv(img)
    try:
        for name, blob in slots.items():
            f = d / name
            f.write_bytes(blob)
            subprocess.run(['mcopy', '-o', str(f), f'z:/WW/{name}'], env=env,
                           check=True, capture_output=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def extract_ww(img, dest):
    """Каталог WW из образа -- для приёмки того, что получилось."""
    dest.mkdir(parents=True, exist_ok=True)
    env, d = mtenv(img)
    try:
        subprocess.run(['mcopy', '-s', '-n', '-o', 'z:/WW/*', str(dest)],
                       env=env, capture_output=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return dest


def holders(path):
    """Кто держит файл открытым -- (pid, имя процесса)."""
    out = []
    for pd in pathlib.Path('/proc').iterdir():
        if not pd.name.isdigit():
            continue
        try:
            for fd in (pd / 'fd').iterdir():
                if fd.resolve() == path:
                    out.append((pd.name, (pd / 'comm').read_text().strip()))
                    break
        except (PermissionError, OSError):
            continue
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    fresh = '--fresh' in sys.argv[1:]
    src = pathlib.Path(args[0] if len(args) > 0 else ROOT / 'game/WordsWorth.hdi').resolve()
    out = pathlib.Path(args[1] if len(args) > 1 else ROOT / 'game/WordsWorth_play.hdi').resolve()
    cfgf = DIST / 'patch.json'

    if not src.is_file():
        sys.exit(f'нет исходного образа {src}')
    if not cfgf.is_file():
        sys.exit(f'нет {cfgf} -- сначала tools/make_patch.py')
    if src == out:
        sys.exit('исходник и итог -- один файл; оригинал должен остаться нетронутым')
    busy = holders(out)
    if busy:
        sys.exit('образ открыт: ' + ', '.join(f'{c} (pid {p})' for p, c in busy) +
                 ' -- закрой эмулятор, переписывать .hdi под ним нельзя')
    cfg = json.loads(cfgf.read_text())

    print(f'исходник {src.name} ({src.stat().st_size} б)')
    have = md5(src)
    if have == cfg['base_md5']:
        print('  образ тот самый, на котором собран патч')
    else:
        print(f'  ⚠️ md5 не как у эталона ({have[:8]} против {cfg["base_md5"][:8]}) --'
              ' идём по файлам, они и рассудят')

    # --- слоты сохранения: решаем ОДИН раз, чей прогресс поедет в образ --------------
    # Либо твой из прежнего образа, либо пустой шаблон. Третьего (чужой сейв из патча) нет.
    if out.is_file() and not fresh:
        slots = read_slots(out)
        empty = empty_slot(src)
        played = [n for n, b in slots.items() if latinise(b)[0] != latinise(empty)[0]]
        print(f'сохранение из {out.name}: слотов {len(slots)}, с прогрессом '
              f'{", ".join(played) if played else "нет"}')
    else:
        slots = {n: empty_slot(src) for n in SLOTS}
        print('слоты чистые' + (' (--fresh, прогресс не переносится)' if fresh else
                                ', это первая сборка'))

    tmp = out.with_suffix(out.suffix + '.tmp')
    shutil.copyfile(src, tmp)
    try:
        subprocess.run([sys.executable, str(ROOT / 'tools/apply_patch.py'),
                        str(tmp), str(DIST)])

        # Слоты кладём ПОВЕРХ патча и правим в них ТОЛЬКО имена -- прогресс не наш.
        renamed = []
        fixed = {}
        for name, blob in slots.items():
            fixed[name], changed = latinise(blob)
            if changed:
                renamed.append(name)
        write_slots(tmp, fixed)
        print(f'слоты записаны; имена латиницей поправлены в: '
              f'{", ".join(renamed) or "уже были"} ({", ".join(NAME_SLOTS.values())})')

        # --- приёмка: сверяем КАЖДЫЙ файл ----------------------------------------------
        d = pathlib.Path(tempfile.mkdtemp(prefix='wwplay.'))
        got = extract_ww(tmp, d / 'ww')
        bad = []
        for e in cfg['entries']:
            f = got / e['name']
            if not f.is_file():
                bad.append((e['name'], 'нет в образе'))
            elif e['name'] in SLOTS:
                pass                      # слоты проверяются ниже, у них своя мера
            elif md5(f) != e['md5_after']:
                bad.append((e['name'], 'содержимое не то'))
        # слот -- это прогресс, он и ОБЯЗАН отличаться от эталонного патча. Мера другая:
        # доехал байт в байт тем, что положили, и имена в нём латинские.
        for name, blob in fixed.items():
            cur = (got / name).read_bytes()
            if cur != blob:
                bad.append((name, 'слот доехал не тем'))
            elif not is_latin(cur):
                bad.append((name, 'имена остались катаканой'))
        shutil.rmtree(d, ignore_errors=True)
        if bad:
            print(f'\n❌ проверка не прошла: {len(bad)} из {len(cfg["entries"])}')
            for n, why in bad[:15]:
                print(f'   {n}: {why}')
            tmp.unlink(missing_ok=True)
            return 1
        tmp.replace(out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    print(f'\n✅ все {len(cfg["entries"])} файлов на месте и совпали'
          + ', слоты на месте')
    print(f'играть: {out}')
    return 0


# ⚠️ Сборка -- ТОЛЬКО при запуске файлом. Раньше `sys.exit(main())` стоял на уровне модуля,
# и обычный `import build_play` (хоть ради одной `holders()`) молча пересобирал образ, в
# который человек сейчас играет. Поймано на себе.
if __name__ == '__main__':
    sys.exit(main())
