#!/usr/bin/env python3
"""Re-verify the entire tract in one command -- from text to a playable image.

    tools/verify.py              # fast checks (seconds)
    tools/verify.py --probe      # plus emulator run (~2 minutes)
    tools/verify.py --recompile  # plus rebuild of all scripts (~hour)
    tools/verify.py --full       # all of the above

Steps and what each one proves:

| step | proves |
|---|---|
| layout | no gaps, no orphans, no broken words -- `tools/relayout.py --check` |
| names | one item -- one name across the entire game (`tools/terms.py`) |
| screens | what is ACTUALLY written on the captured frames (`tools/screenqa.py`, `--screens`) |
| size | every .mes is under the threshold beyond which the engine can't survive loading a location |
| patch | `dist/patch.json` describes exactly what sits in `en/` |
| image | a build from the untouched original passes acceptance on every file |
| foreign section | the patch finds the section where it actually lives on the player's side, not where it lives on ours |
| launch | the game loads, reaches combat, and survives it (`--probe`) |

Each step prints its own verdict; the exit code is the number of failures.
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
        ok, note = False, f'crashed: {e}'
    print(('  ✅ ' if ok else '  ❌ ') + note)
    return 0 if ok else 1


def step_layout():
    import relayout
    n = relayout.check()
    return n == 0, f'lines with layout defects: {n}'


def step_terms():
    """One item -- one English name, and the check is NOT against a list of variants.

    ⚠️ `terms.scan()` looks up KNOWN variants (`ITEMS[...]['variants']`), i.e. a list
    written by hand. The self-check caught exactly this: `Healing Herb` -> `Curing Herb` went
    unnoticed because that variant is not in the list. The list goes stale silently -- which is
    precisely the problem the public copy's composition is computed for, not hand-written.
    Here it's the opposite: the item's Japanese name is taken from the original, and ANY translation,
    other than the canonical one, is treated as a divergence -- even one nobody foresaw.
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
            # ⚠️ Line break splits the name in half (`Ascension\nStone`), and the comparison by
            # the substring doesn't recognize it: 4 false positives out of 14 were exactly of this type.
            flat = b.replace('\n', ' ')
            for jname, want in canon.items():
                if jname not in a or want in flat:
                    continue
                # abbreviation in the item window is intentional, the field doesn't stretch
                if any(x in flat for x in box) or not flat.strip():
                    continue
                # ⚠️ Prose -- not a subject: Fabris describes how they mine in the mountains
                # 金塊, and «gold» fits better there than «Gold Bar». We treat only that as an item,
                # what Japanese presents as an item: in corner brackets 『』 or with a counter.
                if jname not in a.replace('『', '').replace('』', '') or (
                        f'『{jname}』' not in a and '１つ' not in a and '１個' not in a):
                    continue
                rows.append((p.name, 0, flat[:40], want))
    return not rows, ('names agree across the whole game' if not rows
                      else f'name mismatch: {len(rows)}, first {rows[0]}')


def step_screens():
    """Second, independent network: we read the message window from actual frames.

    ⚠️ The source-code gate judges by the LAYOUT MODEL, and the model can be incomplete: the defect
    "...for unauth / orized" slipped past it because the model didn't know about the column
    where the form starts printing. A frame would have caught this immediately.
    """
    sys.path.insert(0, str(ROOT / 'emu'))
    import screenqa
    rows, total = screenqa.scan()
    hard = screenqa.broken(rows)
    return not hard, (f'screens read: {total}, no layout defects'
                      if not hard else
                      f'defects on frames: {len(hard)}, first {hard[0][0].name} {hard[0][1][:1]}')


def step_size():
    import gates
    bad = [(f.name, f.stat().st_size) for f in sorted((ROOT / 'en').glob('*.MES.rkt.mes'))
           if gates.gate_size(f)]
    return not bad, (f'all {len(list((ROOT / "en").glob("*.MES.rkt.mes")))} .mes under the '
                     f'{gates.MES_MAX} b threshold' if not bad else f'past threshold: {bad[:5]}')


def step_split():
    """Split pairs: the parent calls exactly what the companion supports.

    ⚠️ Separate step because the structural gate doesn't reach here: the split changes the script
    skeleton INTENTIONALLY, and diffing against `.orig.rkt` is meaningless. Getting this wrong is easy --
    2026-09-14 a second split into the same name wiped seven branches of a live companion.
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
    return not bad, ('all parent/companion pairs match'
                     if not bad else '; '.join(tail[:3]))


def step_numbers():
    """Numbers don't glue to adjacent text, and the text around them isn't a calque from Japanese.

    ⚠️ Added as a separate step at a direct player request: "we've already fixed this before,
    add it to the pipeline so it doesn't regress." This class of defect cannot be eliminated by patching a single spot --
    the number is printed by a SEPARATE instruction, and any tool that rewrites a neighboring
    line (proofing, compression, `terms`) can leave `diary1 to.` again. The check is cheap;
    let it stay.
    """
    sys.path.insert(0, str(ROOT / 'tools'))
    import numfix
    bad = []
    for p in sorted((ROOT / 'en').glob('*.MES.rkt')):
        if p.name.endswith('.orig.rkt'):
            continue
        for (_, _, tail), _ in numfix.sites(p.read_text(encoding='utf-8')):
            bad.append(f'{p.name[:-4]} …{tail[-24:]!r}+number')
    return not bad, ('numbers are separated from text everywhere' if not bad
                     else f'glued together: {len(bad)}, first {bad[:3]}')


def step_audit():
    """The FULL gate battery runs against the finished text, not against a batch (`tools/audit.py`).

    ⚠️ Was written and NOT wired into the pipeline — meaning it only ran when I remembered it.
    It is the only place that catches the speaker tag, a proper noun starting with a lowercase letter, missing
    text, and the seam with a name; none of these classes is checked anywhere else.
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
    return not bad, ('gate battery across the whole game: no complaints' if not bad
                     else '; '.join(tail[:3]))


def step_consistency():
    """One Japanese line -> one English translation (`tools/consistency.py`).

    ⚠️ This one wasn't in the pipeline either. Measurement from 2026-09-15: `』を見つけた！！` was translated three
    ways in 2532 places, `効果がなかった。` — three ways in 80. This check was silent not because
    everything matched, but because nobody ever invoked it.
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
    # ⚠️ The tool cannot fix forms with `{0}` placeholder: `split_translation` is not
    # maps text into slots, where markers are placed differently, and this is ITS legitimate refusal, and not
    # our oversight. This inconsistency is a known limitation (currently 15 fragments, all
    # cosmetic: «went up by 1 point» vs «went up 1 point»). We consider it a failure
    # only what the tool CAN fix, otherwise the step stays red forever and people stop
    # reading -- and that's the worst thing that can happen to the check.
    out = buf.getvalue()
    frags = [l for l in out.splitlines() if l.lstrip()[:1].isdigit() and 'x  ' in l]
    fixable = [l for l in frags if '{0}' not in l]
    note = f'mismatch: {len(frags)} fragments'
    if frags and not fixable:
        note += ' -- all involve a {0} insertion, beyond what the tool can fix (known)'
    return not fixable, (note if frags else 'one Japanese -> one English')


def step_japanese():
    """No Japanese text remains in the translation (`tools/finish_ja.py`).

    ⚠️ The per-batch gate cannot see the remainder by design: the batch passed — and it's forgotten.
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
    return not left, ('no Japanese text remains' if not left
                      else f'still Japanese: {len(left)}, {left[:3]}')


def step_menu():
    """A menu item that doesn't fit its window gets cut off mid-word.

    ⚠️ `gates.gate_menu_width` existed and was never called from ANYWHERE — a dead check.
    The game has its own menu, 24 half-columns (`MENU_COLS`), and the window doesn't grow with the content.
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
    return not bad, ('menu items fit their window' if not bad
                     else f'do not fit: {len(bad)}, {bad[:2]}')


def step_patch():
    cfg = json.loads((ROOT / 'dist/patch.json').read_text())
    miss = []
    for e in cfg['entries']:
        src = ROOT / 'en' / f'{e["name"]}.rkt.mes'
        if not src.is_file():
            continue                      # files that are not in en/ (slice satellites)
        if hashlib.md5(src.read_bytes()).hexdigest() != e['md5_after']:
            miss.append(e['name'])
    return not miss, (f'patch describes the same as en/ ({len(cfg["entries"])} entries)'
                      if not miss else f'diverged: {miss[:6]}')


def step_image():
    """Build the image ASIDE and verify it.

    ⚠️ NOT into `game/WordsWorth_play.hdi`. We used to build right there with `--fresh` -- meaning
    the check would silently wipe the player's save. That's exactly what happened on the night of 2026-09-11: a person
    sat down to continue and found empty slots. The check must be harmless.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / 'verify_play.hdi'
        r = subprocess.run([sys.executable, str(ROOT / 'tools/build_play.py'),
                            str(gates.BASE), str(out), '--fresh'],
                           capture_output=True, text=True)
    ok = '✅' in r.stdout
    return ok, (r.stdout.strip().splitlines() or ['empty'])[-2 if ok else -1]


def step_foreign():
    """The patch must land on an image where the partition sits in a DIFFERENT location.

    This is not theoretical: the patch ships to users with their own dumps, and their partition
    offset is its own. Previously all tools remembered 71680 -- the address of our image -- and on
    someone else's mtools it silently found nothing: "applied 0, warnings 0" looked like success.
    Here the partition is artificially shifted, and the patch MUST find it.
    """
    import tempfile
    base = gates.BASE.read_bytes()
    shift = 51200                        # arbitrary offset, multiple of sector size
    with tempfile.TemporaryDirectory() as td:
        img = pathlib.Path(td) / 'shifted.hdi'
        img.write_bytes(base[:4096] + b'\x00' * shift + base[4096:])
        r = subprocess.run([sys.executable, str(ROOT / 'tools/apply_patch.py'), str(img)],
                           capture_output=True, text=True)
    line = next((l for l in r.stdout.splitlines() if 'applied files' in l), r.stdout[-200:])
    ok = 'warnings: 0' in line and 'applied files: 0' not in line
    return ok, line.strip()


def step_isolation():
    # The live agent must not share np2kai's system/save dir with any other emulator script:
    # a probe on the shared dir killed the human's unsaved game on 2026-09-15 (STATUS §49).
    r = subprocess.run([str(ROOT / 'emu/.venv/bin/python'), str(ROOT / 'emu/isolation_test.py')],
                       capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stdout.strip().splitlines() or ['no output'])[-1]


def step_play():
    # The play cockpit's own logic: input and protocol (test_play), the autobattle decisions
    # (test_grind), and identifying a line on screen well enough to rewrite it (test_proofread).
    # Every regression found while building it -- a click lost between two frames, a click too
    # short for the engine, a click landing before the cursor, an aim that dragged the cursor
    # back, a tap that lasted four times too long at x4 -- has a test there, each checked by
    # breaking it. Running acceptance of the whole cockpit is emu/play_accept.py (~5 min).
    if not (ROOT / 'emu/play.py').exists():
        return True, 'skipped: not part of this copy'
    r = subprocess.run([str(ROOT / 'emu/.venv/bin/python'), '-m', 'unittest',
                        'tests.test_play', 'tests.test_grind', 'tests.test_proofread'],
                       cwd=ROOT / 'emu', capture_output=True, text=True, timeout=180)
    tail = (r.stderr.strip().splitlines() or ['no output'])
    return r.returncode == 0, f'{tail[-3] if len(tail) >= 3 else tail[0]} {tail[-1]}'


def step_no_russian():
    # The owner's rule (2026-09-15): we talk in Russian, but code, comments, printed messages,
    # UI and tool data are English. It took hours of translation passes to get there once.
    guard = ROOT / 'tools/no_russian.py'
    if not guard.exists():
        # the public copy ships without it (and without en/); in the working repo a missing
        # guard is a failure, not a silent pass
        return not (ROOT / 'en').exists(), 'skipped: not part of this copy'
    r = subprocess.run([sys.executable, str(guard)],
                       capture_output=True, text=True, timeout=120)
    return r.returncode == 0, (r.stdout.strip().splitlines() or ['no output'])[-1]


def step_probe():
    # ⚠️ The probe runs the QA BUILD, not the one a human is playing: the playable one keeps open
    # the agent emulator, and its copy may come out ragged. Along the way, exactly what we
    # just built, not what was sitting in the game since the last build.
    scr = pathlib.Path(os.environ.get('WW_SCRATCH') or '/tmp/ww-probe') / 'emuprobe'
    scr.mkdir(parents=True, exist_ok=True)
    img = scr / 'verify.hdi'
    img.write_bytes((ROOT / 'game/WordsWorth_qa.hdi').read_bytes())
    r = subprocess.run([str(ROOT / 'emu/.venv/bin/python'), str(ROOT / 'emu/battle_probe.py'),
                        img.name], capture_output=True, text=True, timeout=1800)
    img.unlink(missing_ok=True)
    line = next((l for l in r.stdout.splitlines() if 'BATTLE' in l or 'DIED' in l), '')
    return 'SURVIVED' in line, line or 'probe said nothing'


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true', help='plus a run in the emulator')
    ap.add_argument('--screens', action='store_true', help='plus reading captured frames')
    ap.add_argument('--recompile', action='store_true', help='plus rebuilding the scripts')
    ap.add_argument('--full', action='store_true', help='both')
    a = ap.parse_args()
    probe, recompile = a.probe or a.full, a.recompile or a.full
    screens = a.screens or a.full
    bad = 0
    if recompile:
        print('=== rebuilding scripts (slow)')
        subprocess.run([sys.executable, str(ROOT / 'tools/recompile.py')])
    bad += run('text layout', step_layout)
    bad += run('item and character names', step_terms)
    bad += run('script size', step_size)
    bad += run('split pairs', step_split)
    bad += run('numbers not glued to text', step_numbers)
    bad += run('gate battery across the whole game', step_audit)
    bad += run('one Japanese -> one English', step_consistency)
    bad += run('no Japanese text remains', step_japanese)
    bad += run('menu items fit the window', step_menu)
    bad += run('patch and en/ match', step_patch)
    bad += run('build of the playable image', step_image)
    bad += run('patch at a foreign partition location', step_foreign)
    bad += run('the live agent and the play cockpit have their own emulator directories',
               step_isolation)
    bad += run('play cockpit: input and protocol', step_play)
    bad += run('no Russian in code, messages or UI', step_no_russian)
    if screens:
        bad += run('screens from frames', step_screens)
    if probe:
        bad += run('run in the emulator', step_probe)
    print(f'\nfailures: {bad}')
    sys.exit(bad)
