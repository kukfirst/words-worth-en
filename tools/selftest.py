#!/usr/bin/env python3
"""Validate the CHECKS: break one defect of each class and confirm they actually fire.

## Why

Nine checks have accumulated, and each was written after its corresponding defect had
already reached a player. A silent check is indistinguishable from working text, so "all
green" means nothing until it is proven that the green can actually change.

Here, on a COPY of the `en/` directory, one thing is broken at a time, and for each
one it is verified that exactly the step written for it catches it. Not caught — that is a test failure, not
a minor issue: it means the defect class has become invisible again.

⚠️ The working `en/` is not touched: everything happens in a temporary directory, and the tools
are pointed at it via `WW_EN`... which they don't have. So simpler: the directory
is swapped out for the duration of the run and restored in `finally` -- and this is the only place where
such a swap is permissible.

    tools/selftest.py            # all classes
    tools/selftest.py numbers    # one
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
    """First file where the sample is found."""
    for p in sorted(EN.glob(files)):
        if skip_orig and p.name.endswith('.orig.rkt'):
            continue
        s = p.read_text(encoding='utf-8')
        m = re.search(pat, s)
        if m:
            return p, s, m
    return None, None, None


# (class name, how to break, what catches it, what should be raised)
def break_speaker():
    p, s, m = find(r'"\[[A-Z][a-z]+\]: ')
    p.write_text(s[:m.start()] + '"' + s[m.end():], encoding='utf-8')
    return f'{p.name}: speaker tag stripped'


def break_lowercase_name():
    p, s, m = find(r'"\[Club\]:')
    p.write_text(s[:m.start()] + '"[club]:' + s[m.end():], encoding='utf-8')
    return f'{p.name}: lowercase name'


def break_number():
    p, s, m = find(r'"HP restored by "')
    p.write_text(s[:m.start()] + '"HP restored by"' + s[m.end():], encoding='utf-8')
    return f'{p.name}: number glued to text'


def break_charset():
    p, s, m = find(r'"\[[A-Z][a-z]+\]: [A-Z]')
    p.write_text(s[:m.end()] + 'Ж' + s[m.end():], encoding='utf-8')  # ru-data: a glyph outside the game charset
    return f'{p.name}: character outside game encoding'


def break_layout():
    """A word that doesn't fit and will break."""
    p, s, m = find(r'"\[[A-Z][a-z]+\]: [A-Za-z ,\.]{30,}"')
    long = '"' + m.group(0)[1:-1] + ' ' + 'x' * 40 + '"'
    p.write_text(s[:m.start()] + long + s[m.end():], encoding='utf-8')
    return f'{p.name}: line does not fit the window'


def break_split():
    """Remove a branch from the satellite: the parent references what no longer exists."""
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
        return f'{p.name}: branch removed from companion'
    return None


def break_way_home():
    """A companion that calls a battle and then just returns (the Delta loop, 2026-09-17)."""
    import split
    for p in sorted(EN.glob('*.MES.rkt')):
        s = p.read_text(encoding='utf-8')
        m = re.search(r'\n[ \t]*\(mes-jump "[^"]+"\) ' + re.escape(split.WAY_HOME), s)
        if m and not (EN / f'{p.name[:-4]}.orig.rkt').exists():
            p.write_text(s[:m.start()] + s[m.end():], encoding='utf-8')
            return f'{p.name}: jump back to the parent removed after a battle'
    return None


def break_size():
    """File past the threshold. We corrupt the COMPILED .mes: `step_size` points exactly at it,
    and a rebuild just for a test would cost twenty minutes."""
    import gates
    p = EN / 'FLOOR00.MES.rkt.mes'
    p.write_bytes(p.read_bytes() + b'\0' * (gates.MES_MAX + 100 - p.stat().st_size))
    return f'{p.name}: inflated past threshold {gates.MES_MAX}'


def break_terms():
    """One item is named differently — inconsistency across the whole game.

    ⚠️ The spot is found the SAME way the check finds it (`audit.unsplit_forms`): the item in
    quotes 『消炎草』 only appears in the split and combat files, where the form rows don't
    match the original. Direct comparison of `forms()` at such spots finds nothing at all, and
    the first version of the test declared the check blind, when it was the test that was blind.
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
            # ⚠️ We port THIS exact form, by its slots. Hit the first occurrence in the file.
            # no: it ends up in a different form, where Japanese renders the thing as prose, and
            # the check is legitimately silent -- but the test declares it blind. Need to fix where
            # checking.
            fp = EN / owner
            src = fp.read_text(encoding='utf-8')
            a0, b0 = f['slots'][0]
            piece = src[a0:b0].replace('Healing Herb', 'Curing Herb')
            if piece == src[a0:b0]:
                continue                      # nothing to break -- but we can't stay silent about this
            fp.write_text(src[:a0] + piece + src[b0:], encoding='utf-8')
            return f'{owner}: item renamed in form {src[a0:b0][:30]!r}'
    return None


def break_text_loss():
    """Missing text: the satellite's line has been erased."""
    for p in sorted(EN.glob('FLOOR0*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        s = p.read_text(encoding='utf-8')
        m = re.search(r'\(text "[A-Za-z][^"]{30,}"\)', s)
        if m:
            p.write_text(s[:m.start()] + '(text "")' + s[m.end():], encoding='utf-8')
            return f'{p.name}: line emptied'
    return None


CASES = {
    'speaker':   (break_speaker,       'audit', 'speaker tag'),
    'name':      (break_lowercase_name, 'audit', 'lowercase name'),
    'numbers':   (break_number,        'verify:step_numbers', 'glued together'),
    'encoding':  (break_charset,       'audit', 'encoding'),
    'layout':    (break_layout,        'verify:step_layout', 'layout defects: '),
    'split':     (break_split,         'verify:step_split', 'no such branch'),
    'way home':  (break_way_home,      'verify:step_split', 'loses the way back'),
    'size':      (break_size,          'verify:step_size', 'past threshold'),
    'names':     (break_terms,         'verify:step_terms', 'mismatch'),
    'missing':   (break_text_loss,     'audit', 'emptied'),
}


def run_check(which):
    """Run the check and return its output."""
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
    print(f'working directory copy: {backup}\n')
    good = bad = 0
    try:
        for name, (breaker, checker, expect) in CASES.items():
            if only and name != only:
                continue
            shutil.rmtree(EN)
            shutil.copytree(backup / 'en', EN)
            what = breaker()
            if what is None:
                print(f'  {name:11} ⚠️ nothing to break -- class not represented')
                continue
            out = run_check(checker)
            caught = expect in out and 'OK ' not in out.split('\n')[0]
            # for audit the flag is different: the claim is named
            if checker == 'audit':
                caught = expect in out
            print(f'  {name:11} {"✅ caught" if caught else "❌ MISSED"}  ({checker})')
            print(f'              broken: {what}')
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
        print('\nworking directory restored')
    print(f'\n{"✅ all checks catch their class" if not bad else f"❌ blind checks: {bad}"}'
          f'  (checked {good + bad})')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
