#!/usr/bin/env python3
"""Реплика с экрана -> её место в исходнике: файл, строка, сама форма.

Зачем. Чтобы попросить «вот эту фразу переформулировать», нужно однозначно назвать место.
Пересказ на слух не годится: одна и та же фраза встречается в разных файлах и под разными
условиями. Здесь экранный текст сводится к ключу и ищется по всем `en/*.MES.rkt`.

Ключ -- текст реплики, каким его нарисует игра (`tools/render.py`, тот же симулятор, что
сверен с кадром), с подстановкой имени, заменённой на \\x01. На экране вместо \\x01 стоит
настоящее имя, поэтому при поиске имена героев подставляются обратно.

Указатель кладётся в `text_index.json` и пересобирается, если хоть один `en/*.MES.rkt`
новее указателя.
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
    """Строки с экрана -> {'file','line','form'} или None."""
    idx = index() if idx is None else idx
    k = _key(" ".join(lines))
    for n in sorted(names, key=len, reverse=True):
        if n:
            k = k.replace(n, MARK)
    hit = idx.get(k)
    if hit:
        return {"file": hit[0], "line": hit[1], "form": hit[2], "exact": True}
    # ⚠️ Часть реплик игра собирает из кусков: имя печатает один опкод, хвост -- другой
    # (`SENTO00.MES`: `(define-proc 43 (<> (text " were injured!!")))`). Целиком такой
    # строки в исходнике нет, поэтому ищем самый длинный кусок, который в неё уложился.
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
    print(f"реплик в указателе: {len(i)}")
    for p in sys.argv[1:] or [str(HERE / "live.png")]:
        ls = textbox.lines(Image.open(p).convert("RGB"))
        print(f"--- {p}")
        for l in ls:
            print(f"   |{l}|")
        print("   ->", locate(ls, idx=i))
