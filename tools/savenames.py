#!/usr/bin/env python3
"""Hero names inside the save slot.

Character names live NOT in the scripts but in the save: `アストラル` (Astral) and
`ポルックス` (Pollux) don't occur in a single .mes -- neither Japanese nor translated --
they live in `FLAG0..FLAG4` instead, five slots of 3072 b each (the very ロード1..ロード5
of the title menu). The engine substitutes the name into a line at runtime, so katakana
shows up in the English text, and no amount of editing the translation fixes that.

Hence the reason this module exists: a save slot can't simply be taken ready-made from the
patch -- that would kill the player's progress on every rebuild. The correct operation is to
rewrite the TWO FIELDS inside the player's OWN slot and leave the rest of the slot untouched.
`latinise()` does exactly that, and it's idempotent: a field already in Latin is skipped.
"""
NAME_SLOTS = {0x576: "Astral", 0x58a: "Pollux"}   # offset in FLAG* -> name in Latin
NAME_FIELD = 20                                   # stride between fields


def encode(text):
    """Latin text as codes from the game's English font.

    The rule was pulled from MEASUREMENT, not derived from a character table: a file where
    all 234 lines were replaced with "Astral" was built without a dictionary, and the most
    common byte sequence in it occurred exactly 234 times. In the script it's stored as
    (leader-0x20, second); in the save, without the subtraction.
    Column = ASCII-0x20; leader 0x85; second is 0x3F+column, and from column 64 on, 0x40+column.
    """
    out = bytearray()
    for ch in text:
        col = ord(ch) - 0x20
        if not 1 <= col <= 94:
            raise ValueError(f"outside the English character set: {ch!r}")
        out += bytes((0x85, (0x3F if col <= 63 else 0x40) + col))
    return bytes(out)


# ⚠️ A check that can actually fail: the measured "Astral" bytes must match.
assert encode("Astral") == bytes.fromhex("8560859385948592858185 8c".replace(" ", "")), \
    "Latin encoding diverged from measurement -- do NOT touch saves"


def decode(blob):
    """Name field -> Latin text. Inverse of `encode`; a zero or garbage byte ends the name.

    Needed not for saves but for READING: the hero's name is typed in by the player on
    `NAME.MES`, and the `{0}` substitution in a line is exactly that name. Without this,
    screen text can't be traced back to the source
    (`emu/textsrc.py`).
    """
    out = []
    for i in range(0, len(blob) - 1, 2):
        lead, second = blob[i], blob[i + 1]
        if lead != 0x85:
            break
        col = second - (0x3F if second - 0x3F <= 63 else 0x40)
        if not 1 <= col <= 94:
            break
        out.append(chr(col + 0x20))
    return "".join(out)


assert decode(encode("Astral")) == "Astral", "Name parsing diverged from build"


def latinise(blob):
    """(new slot bytes, whether anything changed). We don't touch player progress."""
    b = bytearray(blob)
    changed = False
    for off, latin in NAME_SLOTS.items():
        enc = encode(latin)
        if len(enc) > NAME_FIELD:
            raise ValueError(f"{latin} does not fit the {NAME_FIELD} b field")
        if bytes(b[off:off + len(enc)]) == enc:
            continue
        b[off:off + NAME_FIELD] = enc + b"\x00" * (NAME_FIELD - len(enc))
        changed = True
    return bytes(b), changed


def is_latin(blob):
    """Are the names in the slot already Latin?"""
    return not latinise(blob)[1]
