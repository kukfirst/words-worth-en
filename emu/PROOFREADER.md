# Proofreading inside the game

You can read the translation the way a player meets it -- on the screen, in context -- and fix
a line without leaving the game. The cockpit shows what the message window says, tells you
which entry of your `text/` pack that line is, and writes your correction straight into that
entry.

It never touches the game or the scripts. Your corrections go into `text/*.json`, the same
files you would edit by hand, and reach the game later, on the maintainer's side, through the
usual `tools/check.py` + `tools/import_text.py`.

Installing and running the cockpit itself — the core, the BIOS, the venv, the controls, the
filters, what to do when it misbehaves — is [COCKPIT.md](COCKPIT.md). This page is only about
the proofreading panel.

## What you need

* the patched game image (`.hdi`) -- patch your own copy with the release patch;
* the np2kai libretro core: `/usr/lib/libretro/np2kai_libretro.so` (Arch: `libretro-np2kai-git`;
  elsewhere build it from https://github.com/AZO234/NP2kai), plus its BIOS files in
  `emu/system/np2kai/`;
* Python 3.12+ with `libretro.py`, `numpy` and `pillow` in `emu/.venv`;
* the `text/` pack the maintainer sent you, unpacked next to `emu/` (or anywhere, see `WW_TEXT`).

## Run it

```sh
WW_DISK=/path/to/your/patched.hdi emu/play.sh      # opens http://127.0.0.1:8778/
```

The cockpit listens on your own machine only. Closing or reloading the tab does not stop the
game; `kill $(cat emu/play.pid)` does.

## The panel

**LINE ON SCREEN** is one box, cut to the size of the game's own message window: 56 columns by
four rows. There is no separate editor. The box shows the line the game is drawing, and as soon
as that line is identified as an entry you may rewrite, the same box *becomes* that entry --
same place, same size, nothing appears or slides. The heading says which entry you are looking
at, and `Save to text/` writes it.

What the heading tells you:

* **`YADO.MES:71`** -- one entry, identified exactly. The box is yours to edit.
* **`3 matches`** -- the same words appear in several places in the script (that happens a lot:
  battle lines, shop lines). The button opens the list *over* the page; pick the right one and
  the box switches to it. The entry from the scene you are in is listed first.
* **"≈ part of a line"** -- the game assembled this window from pieces, so there is no single
  entry to write to. The box stays read-only and shows what is on the screen.
* **"no script here to match against"** -- there is no `text/` next to you at all. Point
  `WW_TEXT` at your pack: `WW_TEXT=/path/to/text emu/play.sh`.

Two small things that matter while you work:

* **typing is never interrupted.** The cockpit re-reads the message window several times a
  second. The moment you start editing, the panel **holds**: it stops following the screen and
  says `held · the game keeps running` next to the heading. The game is not paused — only the
  panel is, so the line you are working on cannot slide out from under you. `▶` goes back to
  following the screen and **throws away** what you typed (it asks first if there is anything to
  lose); `⏸` holds a line deliberately, before you start typing.
* **problems show as you type.** The same checker that guards the save runs on what is in the
  box, and a button next to the heading says how many problems it found; click it for the list.
  It turns red only when a save was actually refused.
* **`▾` folds the panel away** when you just want to play, and the cockpit remembers that.

Before anything is written, the line goes through the same checks as `tools/check.py`: the
`{0}` markers, the characters the game's font can draw, the width of the window and word
breaks, the `[Name]:` tag. If a check complains, nothing is written and the reason is shown.

⚠️ **The speaker tag is not yours to edit.** Renaming it, changing its capital letter or
dropping it is refused — including a one-letter slip like `[Innkeper]`. It is who is speaking,
and the game prints it; fix the English after the tag. (This was promised from the start and
only really enforced on 2026-09-16: before that, `tools/check.py` caught the tag only when an
edit split it across two lines.)

Every edit is also appended to `emu/play_edits.jsonl`, so you can see what you changed.

## Checking the cockpit itself

If you want to be sure the whole thing works on your machine before you rely on it:

```sh
emu/.venv/bin/python emu/play_accept.py --system /path/to/your/np2kai --disk /path/to/your.hdi
```

It starts a cockpit of its own on a copy of your image and checks twenty-one things by running
them: the page, the socket, every speed, the mouse, loading a save, telemetry, the map, this
panel (including that a bad line is refused), snapshots, two tabs, autobattle, and an emulator
that is killed mid-game. It never touches a cockpit you already have open.

⚠️ `--system` is not optional here: the PC-98 BIOS and fonts are not part of this repository —
nobody may redistribute them — so point it at the same directory your cockpit uses.

## Sending it back

Run `python3 tools/check.py` for a last look, then send the `text/` folder back the way you
received it. Nothing else in the repository changes while you work.
