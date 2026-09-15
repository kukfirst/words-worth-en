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

INDEX = HERE / "text_index.json"
EN = ROOT / "en"
MARK = "\x01"


def _key(s):
    return re.sub(r"\s+", " ", s).strip()


def build():
    import render
    from strings import forms, unescape
    out = {}
    for f in sorted(EN.glob("*.MES.rkt")):
        if f.name.endswith(".orig.rkt"):
            continue
        src = f.read_text(encoding="utf-8")
        for form in forms(src):
            en = unescape(form["ja"])
            if any(ord(c) > 126 for c in en):
                continue
            ls = render.screen(render.parts_of(en), w=0)
            k = _key(" ".join(ls)).replace("x" * render.NAME, MARK)
            if not k:
                continue
            line = src.count("\n", 0, form["start"]) + 1
            out.setdefault(k, [f.name[:-len(".rkt")], line, en])
    INDEX.write_text(json.dumps(out, ensure_ascii=False))
    return out


def index():
    newest = max((f.stat().st_mtime for f in EN.glob("*.MES.rkt")), default=0)
    if INDEX.exists() and INDEX.stat().st_mtime >= newest:
        try:
            return json.loads(INDEX.read_text())
        except Exception:
            pass
    return build()


def locate(lines, names=("Astral", "Pollux"), idx=None):
    """Lines from the screen -> {'file','line','form'} or None."""
    idx = index() if idx is None else idx
    k = _key(" ".join(lines))
    for n in sorted(names, key=len, reverse=True):
        if n:
            k = k.replace(n, MARK)
    hit = idx.get(k)
    if hit:
        return {"file": hit[0], "line": hit[1], "form": hit[2], "exact": True}
    # ⚠️ Some lines are assembled by the game from pieces: the name is printed by one opcode, the tail by another
    # (`SENTO00.MES`: `(define-proc 43 (<> (text " were injured!!")))`). Exactly like this
    # the string isn't in the source, so we find the longest chunk that fit within it.
    bare = k.replace(MARK, " ")
    best = None
    for key, v in idx.items():
        piece = key.replace(MARK, " ").strip()
        if len(piece) >= 8 and piece in bare and (best is None or len(piece) > best[0]):
            best = (len(piece), v)
    if not best:
        return None
    v = best[1]
    return {"file": v[0], "line": v[1], "form": v[2], "exact": False}


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
