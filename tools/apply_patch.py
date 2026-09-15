#!/usr/bin/env python3
"""Apply the translation patch to the game image.

    tools/apply_patch.py <image.hdi> [dist/]

Patches files INSIDE the image, not the whole image: someone else's dump with a different
sector layout patches just as well, as long as the game files match. A file mismatch is a
warning, not a refusal: the user might have a different edition, and they should learn
exactly which file diverged.
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
        sys.exit(f'missing image {img}')
    cfgf = dist / 'patch.json'
    if not cfgf.is_file():
        sys.exit(f'missing {cfgf}')
    if not shutil.which('xdelta3'):
        sys.exit('xdelta3 not found')
    cfg = json.loads(cfgf.read_text())

    d = pathlib.Path(tempfile.mkdtemp(prefix='wwapply.'))
    mt = d / 'mtoolsrc'
    # ⚠️ The section offset IS LOOKED UP, not remembered: 71680 -- this is the section address of our image,
    # theirs is different, and there mtools silently finds nothing. "Applied 0, warnings
    # 0» yet looks like success.
    off = hdimage.mtoolsrc(img, mt)
    env = os.environ | {'MTOOLSRC': str(mt), 'MTOOLS_SKIP_CHECK': '1'}
    work = d / 'w'; work.mkdir()

    print(f'partition found at offset {off}')
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
                print(f'  ⚠️ not in image: {name}'); warn += 1; continue
            if md5(cur) != e['md5_before']:
                print(f'  ⚠️ file differs from expected: {name}'); warn += 1; continue
            src = work / (name + '.new')
            subprocess.run(['xdelta3', '-d', '-f', '-s', str(cur),
                            str(dist / e['patch']), str(src)], check=True, capture_output=True)
        if md5(src) != e['md5_after']:
            print(f'  ⚠️ result mismatch: {name}'); warn += 1; continue
        subprocess.run(['mcopy', '-o', str(src), f'z:/WW/{name}'], env=env,
                       check=True, capture_output=True)
        ok += 1
    shutil.rmtree(d, ignore_errors=True)
    print(f'applied files: {ok}, warnings: {warn}')
    return 1 if warn else 0

sys.exit(main())
