#!/usr/bin/env python3
"""Line from screen -> its location in source: file, line, the form itself.

Why. To request "rephrase this exact line", you need to name the location unambiguously.
A verbal description won't do: the same phrase appears in different files and under different
conditions. Here the on-screen text is reduced to a key and searched across all `en/*.MES.rkt`.

The key is the line text as the game will render it (`tools/render.py`, the same simulator
that was verified against a frame), with the name substitution replaced by \\x01. On screen,
the real name stands in for \\x01, so during search the character names are substituted back in.

The index is stored in `text_index.json` and rebuilt if any `en/*.MES.rkt`
is newer than the index.
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE))

import os

INDEX = HERE / "text_index.json"
# ⚠️ Built from WHICHEVER text/ WW_TEXT names. A run on a private copy must keep this private
# too (WW_PACK_INDEX), or its index lands next to the code and is served for the real pack.
PACK_INDEX = pathlib.Path(os.environ.get("WW_PACK_INDEX") or HERE / "text_index_pack.json")
EN = pathlib.Path(os.environ.get("WW_EN") or ROOT / "en")
# The proofreader's copy of the script (tools/export_text.py). On a machine that has no
# sources -- a proofreader's -- this is the only body of text there is, and it is enough:
# every entry carries `screen`, the line exactly as the game draws it.
TEXT = pathlib.Path(os.environ.get("WW_TEXT") or ROOT / "text")
MARK = "\x01"
NAME_SLOT = "x" * 6          # render.NAME: the placeholder the hero's name is drawn into
# A speaker tag at the start of the window: "[Pollux]:", "[Nameless Man]:".
TAG = re.compile(r"\[[^\[\]]{1,24}\]:\s*")


def _key(s):
    return re.sub(r"\s+", " ", s).strip()


VERSION = 2          # 1: one entry per key, first writer wins; 2: ALL of them, with the ordinal


def build():
    """Index every form by what it looks like on screen.

    ⚠️ Version 2 keeps EVERY form under a key, not just the first. Measured on this game:
    21 954 forms collapse onto 10 632 keys, 1 340 keys carry more than one form, and version 1
    answered such a key with an arbitrary one of them while calling it exact. That is fine for
    "name a line to reword" and fatal for anything that WRITES to the line it found.

    ⚠️ The ordinal is the form's number in `forms(src)` WITH the skipped ones counted -- that is
    the `FILE#n` id `tools/export_text.py` gives the proofreader, and an edit lands by that id.
    """
    import render
    from strings import forms, unescape
    out = {}
    for f in sorted(EN.glob("*.MES.rkt")):
        if f.name.endswith(".orig.rkt"):
            continue
        src = f.read_text(encoding="utf-8")
        for n, form in enumerate(forms(src)):
            en = unescape(form["ja"])
            if any(ord(c) > 126 for c in en):
                continue
            ls = render.screen(render.parts_of(en), w=0)
            k = _key(" ".join(ls)).replace("x" * render.NAME, MARK)
            if not k:
                continue
            line = src.count("\n", 0, form["start"]) + 1
            out.setdefault(k, []).append([f.name[:-len(".rkt")], line, en, n])
    INDEX.write_text(json.dumps({"version": VERSION, "keys": out}, ensure_ascii=False))
    return out


def build_pack():
    """The same index out of the proofreader's JSON alone -- no `en/*.rkt` needed.

    There is no source line number here (the pack has no sources); the address is the entry's
    own id, `FILE#n`, which is what an edit needs anyway.
    """
    out = {}
    for f in sorted(TEXT.glob("*.json")):
        try:
            rows = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for r in rows:
            k = _key(" ".join(r.get("screen") or [])).replace(NAME_SLOT, MARK)
            name, _, n = str(r.get("id", "")).partition("#")
            if not k or not n.isdigit():
                continue
            out.setdefault(k, []).append([name, None, r.get("en", ""), int(n), "pack"])
    PACK_INDEX.write_text(json.dumps({"version": VERSION, "keys": out}, ensure_ascii=False))
    return out


def sources():
    """What a screen line can be identified against here: 'en', 'pack', or None."""
    if any(EN.glob("*.MES.rkt")):
        return "en"
    if any(TEXT.glob("*.json")):
        return "pack"
    return None


def _cached(path, newest, builder):
    if path.exists() and path.stat().st_mtime >= newest:
        try:
            got = json.loads(path.read_text())
            if isinstance(got, dict) and got.get("version") == VERSION:
                return got["keys"]
        except Exception:
            pass
    return builder()                     # missing, stale, or written by an older version


def index():
    kind = sources()
    if kind == "en":
        newest = max((f.stat().st_mtime for f in EN.glob("*.MES.rkt")), default=0)
        return _cached(INDEX, newest, build)
    if kind == "pack":
        newest = max((f.stat().st_mtime for f in TEXT.glob("*.json")), default=0)
        return _cached(PACK_INDEX, newest, build_pack)
    return {}                            # neither sources nor a pack: nothing to identify against


def key_of(lines, names=("Astral", "Pollux")):
    """Screen lines -> index key: the hero's name back to the placeholder."""
    k = _key(" ".join(lines))
    for n in sorted(names, key=len, reverse=True):
        if n:
            k = k.replace(n, MARK)
    return k


def _entry(v, exact):
    return {"file": v[0], "line": v[1], "form": v[2], "ordinal": v[3],
            "id": f"{v[0]}#{v[3]}", "exact": exact,
            # 'en': built from the sources; 'pack': built from the proofreader's JSON, where
            # `form` is that copy's current text and there is no source to fingerprint against
            "kind": v[4] if len(v) > 4 else "en"}


def candidates(lines, names=("Astral", "Pollux"), idx=None, prefer=None):
    """EVERY source form that renders as these screen lines, best first.

    `prefer` -- a file name (e.g. "YADO.MES"): the scene the game is in right now. Forms from
    it come first, which is what makes an otherwise ambiguous line usable.
    """
    idx = index() if idx is None else idx
    hits = idx.get(key_of(lines, names))
    whole = _key(" ".join(lines))
    tag = TAG.match(whole)
    body = idx.get(key_of([whole[tag.end():]], names)) if tag and not hits else None
    if hits:
        out = [_entry(v, True) for v in hits]
    elif body:
        # ⚠️ The tag is often a form of its own -- `TOWN.MES`: `(text "[" 1 "]:")`, then
        # `(text "This is the town square...")`. When the rest of the window is exactly one
        # indexed form, that form IS the line, as exact as any other; the tag is not in it.
        out = [{**_entry(v, True), "tag": tag.group().strip()} for v in body]
    else:
        # ⚠️ Some lines are assembled by the game from pieces: the name is printed by one opcode,
        # the tail by another (`SENTO00.MES`: `(define-proc 43 (<> (text " were injured!!")))`).
        # Such a string is not in the source, so we look for the longest indexed chunk inside it.
        bare = key_of(lines, names).replace(MARK, " ")
        best, best_len = [], 0
        for key, vs in idx.items():
            piece = key.replace(MARK, " ").strip()
            if len(piece) >= 8 and piece in bare and len(piece) >= best_len:
                if len(piece) > best_len:
                    best, best_len = [], len(piece)
                best.extend(vs)
        out = [_entry(v, False) for v in best]
    if prefer:
        out.sort(key=lambda e: e["file"] != prefer)
    return out


def locate(lines, names=("Astral", "Pollux"), idx=None):
    """Lines from the screen -> {'file','line','form',...} or None.

    ⚠️ Answers with ONE form even when the key is shared (`count` says how many there were).
    Anything that writes must use candidates() instead and refuse an ambiguous line.
    """
    got = candidates(lines, names, idx)
    if not got:
        return None
    return {**got[0], "count": len(got)}


if __name__ == "__main__":
    from PIL import Image
    import textbox
    i = index()
    print(f"lines in the index: {len(i)}")
    for p in sys.argv[1:] or [str(HERE / "live.png")]:
        ls = textbox.lines(Image.open(p).convert("RGB"))
        print(f"--- {p}")
        for l in ls:
            print(f"   |{l}|")
        print("   ->", locate(ls, idx=i))
