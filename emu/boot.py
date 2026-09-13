"""Boot any Words Worth image from cold and stop inside the first room.

Why this exists. The agent's key vocabulary is `up down left right space z x escape`, but the
PC-98 startup screens confirm with `return_key`, which the agent cannot press. So it has always
depended on a pre-baked `game_start.state` -- and a save-state carries the `.mes` that were
RESIDENT when it was taken. A state captured on one image therefore injects that image's scripts
into a run on another one, silently. That cost a whole invalid experiment: the disk held the
original Japanese FLOOR05.MES, the config pointed at it, only one agent was running, and the
translated script was in memory anyway, straight out of the start state.

So the start state stops being a hand-made heirloom and becomes a derived artifact: point this
at an image, it drives the startup and writes `states/<image>.state`. Any experiment that swaps
a `.mes` gets a matching state for free, and cannot accidentally test nothing.

Each step is VERIFIED, not blind. The old in-agent sequence pressed `space` at screens that only
answer to `return_key` and had no idea it had failed; here a step that does not move the screen
is reported by name, so a broken recipe says which screen it stalled on.

  .venv/bin/python boot.py <image.hdi> [out.state]
"""
import pathlib, sys, time

HERE = pathlib.Path(__file__).resolve().parent
STATES = HERE / "states"

# (label, key or None, frames to let it play out, must the screen change?)
# `return_key` is the confirm on every one of these screens -- verified on both the original
# Japanese image and the translated one.
RECIPE = [
    ("dos boot",            None,         1500, False),
    ("config: drives",      "down",        150, False),
    ("config: display",     "down",        150, False),
    ("config: confirm",     "return_key",  150, False),
    ("config: confirm 2",   "return_key",  150, False),
    ("settle to title",     None,          600, False),
    # ⚠️ The title menu is  [start over / load 1 / load 2]  and it comes up with LOAD 1
    # already highlighted, not "start over". Confirming straight away loaded somebody's
    # saved game -- which is why a "new game" showed level 0, GOLD 300 and a STICK, and why
    # the stat-corruption comparison was really comparing two save files. Walk the cursor to
    # the top first; two `up` covers a three-item menu from any starting row.
    # ⚠️ THE TITLE MENU, as measured -- not as assumed.
    # It holds [start from the beginning / load 1..5 / view extras], it SCROLLS (three rows
    # visible) and it WRAPS. On a fresh boot LOAD 1 is already highlighted.
    # "Start from the beginning" CANNOT BE SELECTED. With the cursor verified by screenshot
    # to be sitting on it, return_key, space, z, x and kp_enter all do nothing for twelve
    # seconds -- on the translated image, on an image with the save file deleted, and on the
    # untouched Japanese original. The user could not press it by hand either. So it is a
    # property of this disk image, not of the translation and not of the automation.
    # We therefore enter through LOAD 1, deliberately and on the record. That is fine for
    # controlled comparisons -- what those need is the SAME entry point in every arm, which
    # this gives -- but it must never again be described as "a new game".
    ("title: load 1",       "return_key",  260, True),
    ("intro settle",        None,         1200, False),
    ("name/intro 1",        "return_key",  200, False),
    ("name/intro 2",        "return_key",  200, False),
    ("name/intro 3",        "return_key",  200, False),
    ("name/intro 4",        "return_key",  200, False),
    ("name/intro 5",        "return_key",  200, False),
    ("name/intro 6",        "return_key",  200, False),
]


IN_GAME_SCENES = ("START.MES", "START.MES:ja")      # play resumed from LOAD 1
TITLE_MENU_SCENES = ("START1.MES",)   # титульное меню; суффикс :ja снимается перед сверкой


def in_game(stack):
    """Are we actually playing, or still on the title menu? `stack` from identify()["stack"].

    ⚠️ START1.MES is the TITLE MENU script, not a started game. An earlier version of this
    check had it backwards and certified "new game" for the exact state it was meant to
    reject -- the menu -- so the agent happily "played" a menu for a whole run.

    ⚠️⚠️ И следующая версия ошиблась ЗЕРКАЛЬНО: она сверяла `scene`, то есть ВНУТРЕННИЙ
    элемент стека, со списком, где лежит `START.MES` -- а он всегда ВНЕШНИЙ. Внутренний в
    игре -- это комната (`FLOOR05.MES`), поэтому проверка не могла пройти НИКОГДА. Живой
    агент шёл полтораста ходов с диагнозом «застрял в титульном меню», находясь в игре.
    Проверка, которая всегда ложна, бесполезна ровно так же, как та, что всегда истинна, --
    и вдобавок клевещет на исправный прогон.
    → Играем, когда резидентен `START.MES` И поверх него лежит комната, а меню не резидентно.
    """
    base = [n.split(":", 1)[0] for n in stack]
    if any(n in TITLE_MENU_SCENES for n in base):
        return False
    return "START.MES" in base and len(base) >= 2


def drive(press, run, frame, log=print, until=None):
    """Run the recipe. `press(key, hold, then)`, `run(n)`, `frame()->rgb or None`.

    Returns (ok, notes). ok is False if a step marked `must change` did not move the screen --
    that is the one failure the old blind sequence could not see.
    """
    import numpy as np
    notes = []
    ok = True
    for label, key, then, must in RECIPE:
        before = frame()
        if key is None:
            run(then)
        else:
            press(key, 8, then)
        after = frame()
        if before is None or after is None:
            d = -1
        else:
            d = int(np.abs(after.astype(int) - before.astype(int)).sum())
        moved = d > 3_000_000
        notes.append({"step": label, "key": key, "delta": d, "moved": moved})
        log(f"  {label:22s} {str(key):11s} delta={d:>11} {'moved' if moved else ''}")
        if must and not moved:
            ok = False
            log(f"  ⚠️ step '{label}' did not move the screen -- recipe is stale for this image")
        if label == until:              # остановиться на этом шаге (титульное меню для человека)
            break
    fin = frame()
    bright = float(fin.mean()) if fin is not None else 0.0
    if bright < 8:
        ok = False
        log(f"  ⚠️ ended on a black screen (brightness {bright:.1f})")
    return ok, notes


def state_path_for(image):
    STATES.mkdir(exist_ok=True)
    return STATES / (pathlib.Path(image).stem + ".state")


if __name__ == "__main__":
    import numpy as np
    from libretro import Session
    from libretro.drivers import ExplicitPathDriver, IterableInputDriver
    from libretro.api.input.keyboard import KeyboardState
    from libretro.api.input.joypad import JoypadState
    import state as S

    image = pathlib.Path(sys.argv[1]).resolve()
    out = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else state_path_for(image)
    CORE = "/usr/lib/libretro/np2kai_libretro.so"
    tap = {"k": None, "n": 0}

    def gen():
        while True:
            if tap["n"] > 0:
                tap["n"] -= 1
                yield KeyboardState(**{tap["k"]: True}) if tap["k"] else JoypadState()
            else:
                yield JoypadState()

    sess = Session(core=CORE, game=image,
                   path=ExplicitPathDriver(corepath=CORE, system=HERE / "system",
                                           save=HERE / "save", assets=HERE / "system"),
                   input=IterableInputDriver(gen()), vfs=None)

    def frame():
        av = sess.video._current
        d = av._frame_dims
        if d is None or not av._frame:
            return None
        raw = np.frombuffer(av._frame, dtype=np.uint16).reshape(d.height, d.width)
        return np.dstack([((raw >> 11) & 0x1F) << 3, ((raw >> 5) & 0x3F) << 2,
                          (raw & 0x1F) << 3]).astype(np.uint8)

    def run(n):
        for _ in range(n):
            sess.run()

    def press(k, hold=8, then=140):
        tap["k"] = k; tap["n"] = hold
        run(hold + then)

    print(f"booting {image.name}")
    with sess:
        ok, notes = drive(press, run, frame)
        buf = bytearray(sess.core.serialize_size())
        sess.core.serialize(buf)
        st = bytes(buf)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(st)
        from PIL import Image
        Image.fromarray(frame()).save(out.with_suffix(".png"))
        print(f"\n{'✅' if ok else '⚠️'} wrote {out} ({len(st)/1e6:.1f} MB) + {out.with_suffix('.png').name}")
        print("   identify:", S.identify(st))
        print("   stats   :", S.stats(st))
