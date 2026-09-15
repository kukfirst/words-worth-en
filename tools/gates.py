#!/usr/bin/env python3
"""Mechanical gates. None of these ask the model anything."""
import os, re, subprocess, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from strings import scan

# ⚠️ juice is NOT vendored: it has no license (checked via the GitHub API -- `license: None`),
# so nobody gave us redistribution rights. It gets cloned into `tools/juice` by
# `tools/get_juice.sh` at a pinned commit. The path is looked up next to this file,
# not in the author's home directory; overridable via the `WW_JUICE` env var.
JUICE = pathlib.Path(os.environ.get('WW_JUICE') or
                     pathlib.Path(__file__).resolve().parent / 'juice/mes/juice.rkt')

# ⚠️ The base image is ONE spot for the whole pipeline. The path used to be copied into six
# places (make_patch, build_qa_image, build_play, verify x2, titlemenu), and that was not a
# hypothetical risk: `game/WordsWorth.hdi` had quietly drifted 21 bytes from the image
# declared in README v1.0 -- some run had written a save slot into it. All `md5_before`
# checks were computed against it.
#
# Since 2026-09-15 the base is the Neo Kobe dump (CRC32 8AE7E6F1): that's the one people
# actually download, and the one complained about on gbatemp. The measurement this decision
# rests on: across our three images (working copy, v1.0 set, Neo Kobe), all 906 game files
# are BYTE-IDENTICAL, the only divergence is `FLAG0` -- the save slot. So switching the base
# changes no delta, and the per-file patch applies to any of the three. Overridable via
# `WW_BASE`.
BASE = pathlib.Path(os.environ.get('WW_BASE') or
                    pathlib.Path(__file__).resolve().parent.parent /
                    'game/base/WordsWorth_neokobe.hdi')
# The allowed set is whatever charset "english" actually maps -- read it from
# juice rather than assuming printable ASCII. Notably it has no backslash and no
# tilde, and a stray "\" makes the compiled file unparseable (SENTO0A).
_CHARSET = pathlib.Path(__file__).resolve().parent / 'juice/mes/charset/_charset_english.rkt'
# ⚠️ The fallback is NOT "printable ASCII, eyeballed". The proofreader doesn't have juice
# installed and shouldn't: proofreading is editing text, not building the game. So the
# character set is frozen into `tools/charset_english.json` (208 chars, taken from juice)
# and ships with the tools. Without it the proofreader's charset check would lie both ways.
try:
    ALLOWED = {ord(c) for c in re.findall(r'#\\(.)', _CHARSET.read_text(encoding='utf-8'))}
    ALLOWED |= {0x20}
except OSError:
    import json as _json
    _frozen = pathlib.Path(__file__).resolve().parent / 'charset_english.json'
    try:
        ALLOWED = set(_json.loads(_frozen.read_text(encoding='utf-8')))
    except OSError:
        ALLOWED = set(range(0x20, 0x7f)) | {0xa5, 0xaf}

def gate_count(ja_list, en_list):
    if len(ja_list) != len(en_list):
        return f'count mismatch: {len(ja_list)} in, {len(en_list)} out'
    return None

def gate_emptied(ja_list, en_list):
    """A line that had content must keep content.

    Whitespace-only lines (　　　　 -- ideographic spaces used as layout) came
    back empty, and juice then dies compiling them with 'max: arity mismatch'.
    """
    bad = [i for i, (a, b) in enumerate(zip(ja_list, en_list)) if a and not b]
    return f'emptied lines: {bad[:6]}' if bad else None


def gate_charset(en_list):
    """Characters the game's own font cannot draw.

    ⚠️ '\\n' is NOT such a character, and treating it as one was a real bug. In the batch
    translator a line never contains one, so nothing showed -- but export_text.py writes a
    reply's internal line break as '\\n', and that made this gate reject every multi-line
    reply, INCLUDING lines already shipped in the game. Found 2026-09-15 on a frame sent by
    the player: the one correct fix for it was refused, and 16 more in FLOOR05 with it.
    The break is our own separator between the form's slots, not a glyph.
    """
    bad = []
    for i, s in enumerate(en_list):
        for ch in s.replace('\n', ''):
            if ord(ch) not in ALLOWED:
                bad.append((i, ch))
                break
    return f'non-charset chars: {bad[:5]}' if bad else None

def gate_glossary(ja_list, en_list, terms):
    """Lock a term only where it is actually a name.

    Katakana proper nouns (Fabrice, Sharon) are names wherever they appear.
    Kanji speaker labels are different: 剣士 is "Swordsman" as a dialogue label
    ［剣士］：, but plain "swordsman" inside a sentence -- demanding the glossary
    form everywhere rejects perfectly good lines (measured on YADO.MES).
    """
    miss = []
    for i, (ja, en) in enumerate(zip(ja_list, en_list)):
        for k, v in terms.items():
            katakana = bool(re.fullmatch(r'[ァ-ヶー]+', k))
            present = (k in ja) if katakana else (f'［{k}］' in ja)
            if present and v.lower() not in en.lower():
                miss.append((i, k, v))
    return f'glossary violations: {miss[:5]}' if miss else None


def _screen_width(t):
    """Half-width cells. Japanese glyphs take two, charset-english ASCII takes one."""
    return sum(1 if ord(c) < 0x2000 else 2 for c in t)


def gate_width(ja_list, en_list, wrap):
    """The engine word-wraps, so a long line is fine -- what matters is that the
    translation does not need noticeably MORE box than the original did, and that
    no single word is too long to wrap."""
    bad = []
    for i, (ja, en) in enumerate(zip(ja_list, en_list)):
        budget = _screen_width(ja) * 1.25 + 24
        if _screen_width(en) > budget:
            bad.append((i, _screen_width(en), int(budget)))
            continue
        longest = max((len(w) for w in en.split()), default=0)
        if longest > wrap:
            bad.append((i, f'unbreakable word {longest}>{wrap}'))
    return f'too wide for the original box: {bad[:4]}' if bad else None


_SKEL_STR = re.compile(r'"(?:[^"\\]|\\.)*"')
# only meta here -- the dictionary is removed later by paren balance, and this
# regex would bite off just its first line and leave the glyphs behind
_SKEL_DROP = re.compile(r'^\s*\(meta\b.*?\)\s*$', re.M | re.S)
_SKEL_FONT = re.compile(r'\(set-arr~ @ 21 (?:\([^()]*\)|[^()])*\)')
# ⚠️ A blind spot, on purpose: the English build sets bit 12 @20 before every
# (number …), otherwise digits draw as half-glyphs (strings.number_fix).
# We cut exactly these two entries, on BOTH sides -- the comparison stays honest.
def _drop_numfix(s):
    """Cut out the @20 bit-12 entries -- a regex can't do it, there are three paren levels."""
    out, i = [], 0
    while True:
        j = s.find('(set-arr~ @ 20', i)
        if j < 0:
            out.append(s[i:]); break
        d, k = 0, j
        while k < len(s):
            d += (s[k] == '(') - (s[k] == ')')
            if d == 0:
                break
            k += 1
        form = s[j:k+1]
        if '4095' in form or '4096' in form:
            # eat the space on the left too, otherwise the cut form leaves a stray
            # separator and the skeletons diverge by exactly that
            out.append(s[i:j].rstrip(' \n\t'))
            i = k + 1
            while i < len(s) and s[i] in ' \n\t':
                i += 1
            # a separator is needed exactly where the next instruction follows, not ')'
            if i < len(s) and s[i] != ')':
                out.append(' ')
        else:
            out.append(s[i:k+1]); i = k + 1
    return ''.join(out)

def skeleton(src: str) -> str:
    """Instruction skeleton: text contents blanked, dict/meta and font-width normalised."""
    s = re.sub(r'\(text-raw[^)]*\)', '(text "")', src)
    s = _SKEL_STR.sub('""', s)
    s = _SKEL_DROP.sub('', s)
    s = _SKEL_FONT.sub('(set-arr~ @ 21 *)', s)
    s = _drop_numfix(s)
    s = re.sub(r'\s+', ' ', s).strip()   # line breaks are not structure
    # the dict differs between original and translation by design; drop it whole
    while True:
        m = re.search(r'\(dict(?:-build)?[\s)]', s)
        if not m:
            break
        depth, i, j = 0, m.start(), m.start()
        while j < len(s):
            if s.startswith('#\\', j):     # a character literal: #\( and #\) are data
                j += 3
                continue
            if s[j] == '(':
                depth += 1
            elif s[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        s = s[:i] + s[j+1:]
    s = re.sub(r'\s+', ' ', s).strip()
    # How a line is chopped into text instructions is not preserved when its content
    # changes -- one (text ...) may compile into two, or a trailing empty one may
    # appear. That is not a logic change. So collapse each RUN of consecutive text
    # instructions into a single canonical token that keeps only the runtime
    # insertions (the `1`/`0` procs spliced into the line) and their order.
    # Everything structural -- waits, conditions, jumps, set-reg, proc calls outside
    # text, instruction order -- still has to match exactly.
    def _run(m):
        # Count runtime insertions across the whole run, wherever they sit. The
        # engine's word-wrap splits a long line into several text instructions at
        # compile time, so an insertion can land mid-run or even lead an
        # instruction: (text "…was\n") (text 1 " without the beard."). What must
        # match is which insertions the block prints, and in what order.
        ins = []
        for body, proc in re.findall(r'\(text([^)]*)\)|\(proc (\d+)\)', m.group(0)):
            if proc:
                ins.append(proc)
            else:
                ins += re.findall(r'(?<![\w"])(\d+)(?![\w"])', body)
        return '(TEXT ' + ' '.join(ins) + ') '
    # note \s* not ' ' -- a text instruction can be the last thing before a
    # closing paren, and requiring a trailing space left it out of the run
    run = r'\(text(?: ""| \d+)*\)\s*'
    s = re.sub(rf'(?:{run})+(?:\(proc \d+\)\s*(?:{run})+)*', _run, s)
    return s



# Measured on FLOOR05.MES (emu/size_ladder.py, emu/bisect_crash.py): 40,092 b survives
# loading, 40,115 b kills the game. Confirmed twice on non-overlapping sets of lines --
# by prefix and by suffix -- so it's about SIZE, not a specific line.
# START.MES (11,104 b) is always resident and loads at 0x95f0, the scene script sits right
# after it at 0xC150 -- the base is the same for every scene, so the ceiling is shared.
#
# ⚠️⚠️ THE LAST SENTENCE WAS AN ASSUMPTION, AND IT'S WRONG. "The base is the same, SO the
# ceiling is shared" is reasoning, not a measurement: only one file was tested from a cold
# start. On 2026-09-14 a player hit a DOS drop-out on `FLOOR02` (38,032 b) -- under the old
# threshold, the gate was green. Control: the Japanese original of the same file (35,707 b)
# survives, ours doesn't; the called `FLOOR02A` is irrelevant. Size ladder on a reproducible
# crash (`emu/threshold.py`):
#     38,032 ❌   37,591 ✅   36,495 ✅   35,707 ✅
# So THIS room's boundary is 37,591…38,032, over two thousand below the old threshold.
#
# New threshold = 36,958 b, the size of the largest script the game itself ever shipped.
# That's not another extrapolation, but the one number about the buffer that isn't our own
# guess: the original game runs on it in every room. Ten of our files ended up over it --
# fixed by `tools/split.py` (moving branches out, text untouched) and `tools/tighten.py`.
# ⚠️ The threshold depends on the room, and 36,958 is not a proven ceiling, just a safe
# reference point. The one real check was and remains: walk into the room in the emulator.
MES_MAX = 36_958        # STATUS.md §30


def gate_size(mes_path):
    """The compiled scenario must not exceed the engine's buffer.

    This isn't cosmetic: past the threshold the game doesn't "lag" -- it dies on entering
    the location, and BEFORE dying it clobbers the adjacent player data block (str/def
    come back as garbage).
    """
    n = mes_path.stat().st_size
    if n > MES_MAX:
        return (f'compiled {n} b > {MES_MAX} b: the engine will not survive loading this '
                f'location (measured boundary 40,092/40,115)')
    return None

def juice(args, cwd):
    return subprocess.run(['racket', str(JUICE)] + args, cwd=cwd,
                          capture_output=True, text=True, timeout=900)

def _literalised(sk: str) -> str:
    """Normalize the skeleton so "name as a literal instead of dict text" doesn't count as a
    divergence: TEXT and str are the same string output, and string-output forms can be
    dropped or added.

    ⚠️ WHAT THIS STOPS CHECKING: for a file in LITERALISED, neither the choice of string
    opcode nor the presence of string-output forms is compared anymore. Branch order, calls,
    procedures and register handling are still compared as before -- and that's exactly
    where the breakage lives that this gate was built to catch. In place of what was
    dropped: emu/item_probe.py grabs the name box on a frame, emu/save_probe.py checks that
    the resident script is alive.
    """
    s = sk.replace('TEXT', 'str')
    s = re.sub(r'\s*""', '', s)                       # string contents already zeroed by skeleton()
    s = re.sub(r'\((?:str|text)\s*\)\s*', '', s)      # empty string-output form
    return s


# The item-name field doesn't decompress the .MES dictionary, so English names are written
# as a literal (str …), not (text …) -- otherwise the screen shows half-kanji. Measurements
# and analysis: STATUS.md §13; the fix lives in tools/itembox.py, checked on a frame by
# emu/item_probe.py.
#
# ⚠️ The list isn't hand-enumerated: the block is present in 28 files (START, START1 and 26
# battle SENTO*), and a hardcoded set would drift from them at the first change. The marker
# is the block itself, present in the translated source.
_ITEMBOX = '((== (~ @ 23) 0)'


def literalised(workdir: pathlib.Path, name: str) -> bool:
    src = workdir / f'{name}.rkt'
    return src.exists() and _ITEMBOX in src.read_text(encoding='utf-8')


def gate_compile_and_structure(workdir: pathlib.Path, name: str):
    """name like FLOOR01.MES. Requires <name>.rkt (translated) and <name>.orig.rkt."""
    r = juice(['-cf', f'{name}.rkt'], workdir)
    mes = workdir / f'{name}.rkt.mes'
    if not mes.exists() or mes.stat().st_size == 0:
        return f'compile failed: {r.stderr.strip()[:300] or r.stdout.strip()[:300]}'
    tmp = workdir / 'roundtrip'
    tmp.mkdir(exist_ok=True)
    (tmp / name).write_bytes(mes.read_bytes())
    # must mirror how work/*.rkt were produced: --protag 0,1 (NOT -p ww, which
    # forces protag 0 and un-fuses name insertions), and english charset for
    # translated text, else juice emits text-raw.
    r = juice(['-df', '--protag', '0,1', '--charset', 'english', name], tmp)
    back = tmp / f'{name}.rkt'
    if not back.exists():
        return f'recompiled file will not decompile: {r.stderr.strip()[:200]}'
    # Declared logic fixes (tools/logicfix.py) are applied to the REFERENCE too: that way
    # exactly the declared change is allowed, and anything else is still caught.
    import logicfix
    a = skeleton(logicfix.fixed(name, (workdir / f'{name}.orig.rkt').read_text(encoding='utf-8')))
    b = skeleton(back.read_text(encoding='utf-8'))
    if a != b and literalised(workdir, name):
        a, b = _literalised(a), _literalised(b)
    if a != b:
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return f'structure diverges at char {i}: orig …{a[max(0,i-60):i+60]}… new …{b[max(0,i-60):i+60]}…'
        return f'structure length differs: {len(a)} vs {len(b)}'
    return gate_size(mes)

_MARK = re.compile(r'\{(\d+)\}')
_ATTACH = 'はがをにへともで、'      # particles that bind to whatever was printed just before


def gate_edges(ja_list, en_list):
    """A name or number the engine prints hard against the line must not weld to a word.

    Two shapes, one failure. `(text 0 "は『石板のかけら』…")` carries the insertion inside the
    form, so the English carries a {0} -- but the marker being present proves nothing: what
    matters is the character right AFTER it, because that is what the name butts against.
    When the name comes from a preceding proc there is no marker at all and the only clue
    is the particle the Japanese opens with; then the English must open with a space
    (or 's, or a comma).

    ⚠️ Report, not a hard gate: a menu item legitimately opens with は ("はい" -> "Yes") and
    the name-entry screen is a kana grid. Callers filter those (tools/repair_edges.py).
    """
    bad = []
    for i, (ja, en) in enumerate(zip(ja_list, en_list)):
        ja, en = ja or '', en or ''
        if not ja.strip() or not en.strip() or '\u3000' in ja:
            continue
        if ja.lstrip().startswith('{'):
            if not en.lstrip().startswith('{'):
                bad.append((i, ja[:48], en[:48]))
                continue
            after = _MARK.sub('', en.lstrip(), count=1)
            if after and (after[0].isalnum() or after[0] == '"'):
                bad.append((i, ja[:48], en[:48]))
        elif ja[0] in _ATTACH and len(ja) > 3 and (en[0].isalnum() or en[0] == '"'):
            bad.append((i, ja[:48], en[:48]))
    return bad

MENU_COLS = 24     # menu row width, taken from the engine itself: proc 13 draws
                   # (box-inv 37 … 60 …) and (box 1 24 24 39) -- 24 half-columns,
                   # the window is fixed and does NOT stretch to fit content.


def menu_spans(src):
    """Byte bounds of each (menu-show …): an entry inside it is a menu row."""
    out = []
    for m in re.finditer(r'\(menu-show\b', src):
        d, i = 0, m.start()
        while i < len(src):
            if src[i] == '(':
                d += 1
            elif src[i] == ')':
                d -= 1
                if d == 0:
                    break
            i += 1
        out.append((m.start(), i))
    return out


def _cols(t):
    """Width in half-columns: a Japanese glyph takes two, a Latin one takes one."""
    return sum(2 if ord(c) > 0x2000 else 1 for c in t)


def gate_menu_width(orig_src, en_src, forms_fn):
    """A menu entry that doesn't fit the window gets cut off mid-word.

    Spotted by a human on screen: "What's the Swordsman's P" -- the window ran out. The
    menu is drawn by proc 13 with a fixed width of 24, so the check is exact, not eyeballed.
    Menus where the ORIGINAL itself is wider than 24 are skipped: that window is drawn by
    different code.
    """
    sp = menu_spans(orig_src)
    fo, fe = forms_fn(orig_src), forms_fn(en_src)
    if len(fo) != len(fe):
        return []
    bad = []
    for a, b in zip(fo, fe):
        span = next((x for x in sp if x[0] <= a['start'] <= x[1]), None)
        if not span:
            continue
        if any(_cols(f['ja']) > MENU_COLS for f in fo
               if span[0] <= f['start'] <= span[1]):
            continue
        w = _cols(b['ja'])
        if w > MENU_COLS:
            bad.append((w, a['ja'][:24], b['ja'][:48]))
    return bad

