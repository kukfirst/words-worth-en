#!/usr/bin/env python3
"""Build the proofreading pack: the game text plus the guide, one zip.

## What is inside, and what is not

| inside | why |
|---|---|
| `text/*.json` | the script itself, flattened, one entry per line of dialogue |
| `PROOFREADING.md` | the guide: what to install (nothing), what to edit, what not to touch |

The tools are NOT included: they live in the public repository, and the proofreader gets them
with `git clone` and keeps them current with `git pull`. That way a fix to the checker reaches
them without waiting for a new pack -- and fixes there are frequent: a `check.py` that refuses
a correct line is a bug in `check.py`, and that has already happened.

The pack is VERIFIED the way the proofreader will use it: unpacked over a clean copy of the
repository and run with no juice, no disk image and no emulator. If it does not work there,
they hit it on their first move and we hear about it a day later over chat.

    tools/proofpack.py            # build into dist/
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PUB = pathlib.Path('/home/runekill/development/words-worth-en')
TEXT = ROOT / 'text'
OUT = ROOT / 'dist' / 'words-worth-proofreading.zip'


def main():
    if not TEXT.is_dir():
        sys.exit(f'no {TEXT} -- run tools/export_text.py first')
    guide = PUB / 'PROOFREADING.md'
    if not guide.exists():
        sys.exit(f'no {guide}')

    files = sorted(TEXT.glob('*.json'))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f'text/{f.name}')
        z.write(guide, 'PROOFREADING.md')
    print(f'{OUT.name}: {len(files)} text files plus the guide, '
          f'{OUT.stat().st_size / 1024 / 1024:.1f} MB')

    # Acceptance, the proofreader's way: unpack over a CLEAN copy of the repository, with no
    # juice, no disk image and no emulator -- exactly what they will have.
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        shutil.copytree(PUB, d / 'repo', ignore=shutil.ignore_patterns(
            '.git', 'juice', '__pycache__', 'text'))
        with zipfile.ZipFile(OUT) as z:
            z.extractall(d / 'repo')
        r = subprocess.run([sys.executable, 'tools/check.py', '--quiet'],
                           cwd=d / 'repo', capture_output=True, text=True, timeout=1800)
        ok = r.returncode == 0 and 'No edits found' in r.stdout
        print(f'proofreader path: unpacked over a clean clone, ran the checker -- '
              f'{"works" if ok else "DOES NOT WORK"}')
        if not ok:
            print((r.stdout + r.stderr).strip()[-400:])
            return 1
    print(f'\nship: {OUT}')
    print('they run: git clone github.com/kukfirst/words-worth-en, unzip the pack inside')
    return 0


if __name__ == '__main__':
    sys.exit(main())
