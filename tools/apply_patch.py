#!/usr/bin/env python3
"""Применить патч перевода к образу игры.

    tools/apply_patch.py <образ.hdi> [dist/]

Правит файлы ВНУТРИ образа, а не образ целиком: чужой дамп с другим расположением
секторов патчится так же успешно, лишь бы файлы игры совпадали. Несовпадение файла --
предупреждение, а не отказ: у пользователя может быть другая редакция, и он должен
узнать, какой именно файл разошёлся.
"""
import hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hdimage

def md5(p):
    return hashlib.md5(pathlib.Path(p).read_bytes()).hexdigest()

def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    img = pathlib.Path(sys.argv[1]).resolve()
    dist = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else
                        pathlib.Path(__file__).resolve().parent.parent / 'dist').resolve()
    if not img.is_file():
        sys.exit(f'нет образа {img}')
    cfgf = dist / 'patch.json'
    if not cfgf.is_file():
        sys.exit(f'нет {cfgf}')
    if not shutil.which('xdelta3'):
        sys.exit('нет xdelta3')
    cfg = json.loads(cfgf.read_text())

    d = pathlib.Path(tempfile.mkdtemp(prefix='wwapply.'))
    mt = d / 'mtoolsrc'
    # ⚠️ Смещение раздела ИЩЕТСЯ, а не помнится: 71680 -- это адрес раздела нашего образа,
    # у чужого он другой, и там mtools молча не находит ничего. «Применено 0, предупреждений
    # 0» при этом выглядит как успех.
    off = hdimage.mtoolsrc(img, mt)
    env = os.environ | {'MTOOLSRC': str(mt), 'MTOOLS_SKIP_CHECK': '1'}
    work = d / 'w'; work.mkdir()

    print(f'раздел найден на смещении {off}')
    ok = warn = 0
    for e in cfg['entries']:
        name = e['name']
        if e['type'] == 'add':
            src = dist / e['file']
        else:
            cur = work / name
            r = subprocess.run(['mcopy', '-o', f'z:/WW/{name}', str(cur)],
                               env=env, capture_output=True)
            if not cur.is_file():
                print(f'  ⚠️ нет в образе: {name}'); warn += 1; continue
            if md5(cur) != e['md5_before']:
                print(f'  ⚠️ файл отличается от ожидаемого: {name}'); warn += 1; continue
            src = work / (name + '.new')
            subprocess.run(['xdelta3', '-d', '-f', '-s', str(cur),
                            str(dist / e['patch']), str(src)], check=True, capture_output=True)
        if md5(src) != e['md5_after']:
            print(f'  ⚠️ результат не совпал: {name}'); warn += 1; continue
        subprocess.run(['mcopy', '-o', str(src), f'z:/WW/{name}'], env=env,
                       check=True, capture_output=True)
        ok += 1
    shutil.rmtree(d, ignore_errors=True)
    print(f'применено файлов: {ok}, предупреждений: {warn}')
    return 1 if warn else 0

sys.exit(main())
