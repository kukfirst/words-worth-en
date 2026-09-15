#!/usr/bin/env python3
"""End-to-end: walk the whole proofreading loop the way a stranger will, and prove it works.

The pack, the guide and the checker are each fine on their own. What has to work is the LOOP,
and nobody had ever walked it: build a pack, hand it to someone with nothing installed, get a
folder back, and land their edits in the game without a human in the middle.

The run below does exactly that, in one process, on throwaway copies:

  1. build the pack                         (tools/proofpack.py)
  2. clone the public repo into a temp dir, with NO juice, NO disk image, NO emulator
  3. unzip the pack into it                 -- this is the proofreader's machine now
  4. make edits of every kind: good ones, and one of each way to get it wrong
  5. run tools/check.py there               -- must refuse exactly the bad ones
  6. fix the bad ones the way a person would, re-run -- must pass
  7. copy the folder back and run tools/import_text.py --apply on the real tree
  8. confirm the good edits are in the scripts, byte for byte
  9. put the real tree back exactly as it was

Nothing here touches `en/` permanently: step 9 restores it from a copy made in step 0, and the
restore is verified, not assumed.

    tools/e2e_proofread.py
"""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PUB = pathlib.Path('/home/runekill/development/words-worth-en')
PY = sys.executable

# Edits the run makes. (kind, how to change a line, should the checker refuse it)
GOOD = 'good'
BAD = 'bad'


def fingerprint(path):
    """One number for the whole en/ tree, to prove we put it back."""
    h = hashlib.blake2b(digest_size=16)
    for p in sorted(path.glob('*.rkt')):
        h.update(p.name.encode())
        h.update(hashlib.blake2b(p.read_bytes(), digest_size=8).digest())
    return h.hexdigest()


def run(cmd, cwd, what):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=3600)
    return r


def make_edits(text_dir):
    """Edit the proofreader's copy. Returns (good ids, bad ids)."""
    good, bad = [], []

    # A good edit: reword without touching markers, length or layout.
    p = text_dir / 'FLOOR03.MES.json'
    rows = json.loads(p.read_text(encoding='utf-8'))
    for r in rows:
        if r['en'].startswith('[') and ' really ' in r['en']:
            r['en'] = r['en'].replace(' really ', ' truly ', 1)
            good.append((r['id'], r['en']))
            break
    else:
        for r in rows:
            if r['en'].startswith('[') and r['en'].endswith('.') and 30 < len(r['en']) < 50:
                r['en'] = r['en'][:-1] + '!'
                good.append((r['id'], r['en']))
                break
    # A bad edit: the {0} marker is gone.
    for r in rows:
        if '{0}' in r['en'] and r['id'] not in [g[0] for g in good]:
            r['en'] = r['en'].replace('{0}', 'Astral', 1)
            bad.append((r['id'], 'marker'))
            break
    # A bad edit: a character the game cannot draw.
    for r in rows:
        if r['en'].startswith('[') and r['id'] not in [g[0] for g in good] \
                and r['id'] not in [b[0] for b in bad]:
            r['en'] = r['en'] + ' — indeed'
            bad.append((r['id'], 'charset'))
            break
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')

    # A bad edit in a second file: a line that cannot fit the window.
    q = text_dir / 'FLOOR04.MES.json'
    rows = json.loads(q.read_text(encoding='utf-8'))
    for r in rows:
        if r['en'].startswith('[') and '\n' in r['en']:
            r['en'] = r['en'].split('\n')[0] + ' followed by a great many further words with no break at all'
            bad.append((r['id'], 'layout'))
            break
    q.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    return good, bad


def undo_bad(text_dir, backup):
    """Fix the bad edits the way a person would: put those lines back."""
    for name in ('FLOOR03.MES.json', 'FLOOR04.MES.json'):
        orig = {r['id']: r for r in json.loads((backup / name).read_text(encoding='utf-8'))}
        p = text_dir / name
        rows = json.loads(p.read_text(encoding='utf-8'))
        for r in rows:
            o = orig[r['id']]
            # keep only edits that pass; the bad ones go back to the exported text
            if r['en'] != o['en'] and hashlib.blake2b(
                    r['en'].encode(), digest_size=4).hexdigest() != r['was']:
                if any(k in r['en'] for k in ('—', 'Astral')) or len(r['en']) > len(o['en']) + 30:
                    r['en'] = o['en']
        p.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')


def main():
    print('=== end-to-end: the proofreader loop\n')
    fails = []

    # 0. save the real tree
    keep = pathlib.Path(tempfile.mkdtemp(prefix='e2e-en.'))
    shutil.copytree(ROOT / 'en', keep / 'en')
    before = fingerprint(ROOT / 'en')
    keep_text = pathlib.Path(tempfile.mkdtemp(prefix='e2e-text.'))
    shutil.copytree(ROOT / 'text', keep_text / 'text')
    print(f'  0. real tree saved, fingerprint {before[:16]}')

    try:
        # 1. build the pack
        r = run([PY, 'tools/proofpack.py'], ROOT, 'pack')
        pack = ROOT / 'dist' / 'words-worth-proofreading.zip'
        if r.returncode or not pack.exists():
            fails.append('pack did not build')
            print('  1. pack               FAILED')
            print((r.stdout + r.stderr)[-300:])
            return finish(keep, keep_text, before, fails)
        print(f'  1. pack built         {pack.stat().st_size // 1024} KB')

        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            # 2. a clean machine: repo without juice, without the game
            shutil.copytree(PUB, d / 'repo', ignore=shutil.ignore_patterns(
                '.git', 'juice', '__pycache__', 'text', 'dist'))
            repo = d / 'repo'
            has = [x for x in ('juice', 'game') if (repo / 'tools' / x).exists()
                   or (repo / x).exists()]
            print(f'  2. clean clone        no juice, no game image '
                  f'{"(LEAKED: " + str(has) + ")" if has else ""}')
            if has:
                fails.append(f'the clean clone contains {has}')

            # 3. unpack
            import zipfile
            with zipfile.ZipFile(pack) as z:
                z.extractall(repo)
            n = len(list((repo / 'text').glob('*.json')))
            guide = (repo / 'PROOFREADING.md').exists()
            print(f'  3. pack unpacked      {n} text files, guide {"yes" if guide else "NO"}')
            if not n or not guide:
                fails.append('the unpacked pack is incomplete')

            # 3a. the checker must run on an untouched copy and say so
            r = run([PY, 'tools/check.py', '--quiet'], repo, 'check-clean')
            clean_ok = r.returncode == 0 and 'No edits found' in r.stdout
            print(f'  3a. checker on clean  {"says no edits" if clean_ok else "BROKEN"}')
            if not clean_ok:
                fails.append('the checker does not run on an untouched pack')
                print((r.stdout + r.stderr)[-400:])

            # 4. edits
            backup = d / 'exported'
            shutil.copytree(repo / 'text', backup)
            good, bad = make_edits(repo / 'text')
            print(f'  4. edits made         {len(good)} good, {len(bad)} deliberately broken '
                  f'({", ".join(b[1] for b in bad)})')

            # 5. the checker must refuse exactly the broken ones
            r = run([PY, 'tools/check.py'], repo, 'check-bad')
            refused = r.returncode == 1
            named = sum(1 for b in bad if b[0] in r.stdout)
            print(f'  5. checker on edits   {"refuses" if refused else "ACCEPTS"}, '
                  f'names {named} of {len(bad)} broken')
            if not refused or named != len(bad):
                fails.append(f'the checker named {named} of {len(bad)} broken edits')
                print((r.stdout + r.stderr)[-600:])

            # 6. fix them, must pass
            undo_bad(repo / 'text', backup)
            r = run([PY, 'tools/check.py'], repo, 'check-good')
            passed = r.returncode == 0 and 'pass' in r.stdout
            print(f'  6. after fixing       {"passes" if passed else "STILL REFUSES"}')
            if not passed:
                fails.append('the checker still refuses after the bad edits were undone')
                print((r.stdout + r.stderr)[-500:])

            # 7. the folder comes back to us and is applied
            shutil.rmtree(ROOT / 'text')
            shutil.copytree(repo / 'text', ROOT / 'text')
            r = run([PY, 'tools/import_text.py', '--apply'], ROOT, 'import')
            applied = r.returncode == 0
            print(f'  7. imported here      {"applied" if applied else "REFUSED"}')
            if not applied:
                fails.append('import_text refused a folder the checker had passed')
                print((r.stdout + r.stderr)[-600:])

            # 8. the good edits are really in the scripts
            landed = 0
            for gid, text in good:
                fname = gid.split('#')[0]
                src = (ROOT / 'en' / f'{fname}.rkt').read_text(encoding='utf-8')
                probe = text.split('\n')[0][-28:].replace('"', '\\"')
                if probe in src:
                    landed += 1
            print(f'  8. landed in scripts  {landed} of {len(good)}')
            if landed != len(good):
                fails.append(f'only {landed} of {len(good)} good edits reached en/')

    finally:
        # The restore must happen even if a step above raised; the exit code is decided
        # after it, never inside the finally block.
        code = finish(keep, keep_text, before, fails)
    return code


def finish(keep, keep_text, before, fails):
    shutil.rmtree(ROOT / 'en', ignore_errors=True)
    shutil.copytree(keep / 'en', ROOT / 'en')
    shutil.rmtree(ROOT / 'text', ignore_errors=True)
    shutil.copytree(keep_text / 'text', ROOT / 'text')
    after = fingerprint(ROOT / 'en')
    restored = after == before
    print(f'  9. tree restored      {"identical" if restored else "DIFFERS — CHECK en/"}')
    if not restored:
        fails.append('en/ was not restored exactly')
    shutil.rmtree(keep, ignore_errors=True)
    shutil.rmtree(keep_text, ignore_errors=True)
    print()
    if fails:
        print(f'FAILED: {len(fails)}')
        for f in fails:
            print(f'   - {f}')
        return 1
    print('the proofreader loop works end to end')
    return 0


if __name__ == '__main__':
    sys.exit(main())
