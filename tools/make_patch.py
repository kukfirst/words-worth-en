#!/usr/bin/env python3
"""Собрать патч перевода: пофайловые xdelta + конфиг, плюс полный образ как запасной.

⚠️ Почему ПОФАЙЛОВО, а не дельтой всего образа. Так делает сообщество PC-98: патчер
Pachy98 (46 OkuMen) правит отдельные файлы внутри диска через NDC + xdelta по JSON-конфигу,
и смысл именно в этом -- «target patch necessary files correctly while ignoring differences
on the rest of the disk, making it possible for end-users to have different dumps or even
make their own dump of a disk». Дельта всего образа сходится только на побайтово том же
дампе, что у нас; пофайловая -- на любом.
Проверено на нашем случае: меняется 84 файла и добавляется 11 (спутники разреза), это
1.7 МБ против 20 МБ образа.

⚠️ IPS не годится в принципе: потолок 16 МБ. BPS рассчитан на картриджи. Для дисковых
образов стандарт -- xdelta3 (VCDIFF).

    tools/make_patch.py            # собрать в dist/
"""
import hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hdimage
import gates

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = gates.BASE                            # нетронутый оригинал (gates.BASE)
# Переведённый образ. Аргументом можно указать другой -- например собранный рядом,
# пока рабочий занят запущенным агентом.
DST = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / 'game/WordsWorth_qa.hdi'
OUT = ROOT / 'dist'


def mount(img):
    d = pathlib.Path(tempfile.mkdtemp(prefix='wwpatch.'))
    cfg = d / 'mtoolsrc'
    hdimage.mtoolsrc(img, cfg)
    env = os.environ | {'MTOOLSRC': str(cfg), 'MTOOLS_SKIP_CHECK': '1'}
    files = d / 'files'
    files.mkdir()
    subprocess.run(['mcopy', '-o', '-s', 'z:/WW/*', str(files)], env=env, capture_output=True)
    return d, files


def md5(p):
    return hashlib.md5(pathlib.Path(p).read_bytes()).hexdigest()


def main():
    if not shutil.which('xdelta3'):
        sys.exit('нет xdelta3: sudo pacman -S --needed xdelta3')
    for p in (SRC, DST):
        if not p.is_file():
            sys.exit(f'нет {p}')
    _, a = mount(SRC)
    _, b = mount(DST)
    OUT.mkdir(exist_ok=True)
    patches = OUT / 'files'
    if patches.exists():
        shutil.rmtree(patches)
    patches.mkdir(parents=True)

    entries, changed, added = [], 0, 0
    for f in sorted(b.iterdir()):
        if not f.is_file():
            continue
        old = a / f.name
        if old.is_file():
            if md5(old) == md5(f):
                continue
            out = patches / (f.name + '.xdelta')
            subprocess.run(['xdelta3', '-e', '-f', '-s', str(old), str(f), str(out)],
                           check=True, capture_output=True)
            entries.append({'name': f.name, 'type': 'patch', 'patch': f'files/{out.name}',
                            'md5_before': md5(old), 'md5_after': md5(f)})
            changed += 1
        else:
            # ⚠️ Спутники разреза -- НОВЫЕ файлы, которых в оригинале нет. Дельту от пустоты
            # делать незачем, кладём как есть: вместе они меньше сотни килобайт.
            shutil.copyfile(f, patches / f.name)
            entries.append({'name': f.name, 'type': 'add', 'file': f'files/{f.name}',
                            'md5_after': md5(f)})
            added += 1

    (OUT / 'patch.json').write_text(json.dumps({
        'game': 'Words Worth (elf, PC-98, 1993-07-22)',
        'target': 'HDI, каталог WW',
        'base_md5': md5(SRC),
        'result_md5': md5(DST),
        'entries': entries}, ensure_ascii=False, indent=1))

    # запасной вариант: дельта всего образа -- работает только на нашем дампе
    full = OUT / 'WordsWorth_en_full.xdelta'
    subprocess.run(['xdelta3', '-e', '-f', '-s', str(SRC), str(DST), str(full)],
                   check=True, capture_output=True)

    size = sum(p.stat().st_size for p in patches.rglob('*'))
    print(f'изменено файлов: {changed}, добавлено: {added}')
    print(f'пофайловый патч: {size/1024:.0f} КБ  ({patches})')
    print(f'полный образ:    {full.stat().st_size/1024:.0f} КБ  ({full.name})')
    print(f'конфиг:          {OUT / "patch.json"}')


main()
