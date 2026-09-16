# The play cockpit

A browser cockpit for playing Words Worth: the game runs in a real emulator core, the page is
the screen, the controls and the instruments. Screen filters, speed control, a floor map drawn
from the game's own memory, save snapshots, an autobattle that walks and fights on its own, and
— for proofreaders — the line the game is showing right now, with an editor for it
([PROOFREADER.md](PROOFREADER.md)).

It is one person's cockpit on one machine. It listens on `127.0.0.1` only and asks nobody who
they are; see [Safety](#safety) before you get clever with port forwarding.

---

## What you need

| | |
|---|---|
| The game | your own patched `.hdi` — patch your own copy with the release patch |
| Core | `np2kai_libretro.so` — Arch: `libretro-np2kai-git`; elsewhere build it from [NP2kai](https://github.com/AZO234/NP2kai) |
| BIOS | the PC-98 BIOS and font files, in a directory of your own (see below) |
| Python | 3.12 or newer, with `libretro.py`, `numpy` and `pillow` |
| Browser | anything with WebGL2 — the filters are shaders |

⚠️ **The BIOS is not in this repository and never will be.** Nobody may redistribute it. The
core needs `np2kai/` with the PC-98 font and BIOS files inside whatever directory you point
`WW_SYSTEM` at; np2kai's own documentation says which files.

## Install

```sh
python3 -m venv emu/.venv
emu/.venv/bin/pip install "libretro.py" numpy pillow
```

That is the whole install. The cockpit has no build step, no bundler and no npm: the page is
three plain files in `emu/play_web/`.

## Run

```sh
WW_DISK=/path/to/your/patched.hdi WW_SYSTEM=/path/to/your/np2kai emu/play.sh
```

`play.sh` starts the cockpit if it is not already up, waits for it to answer, and opens
<http://127.0.0.1:8778/> in your browser.

* **Closing or reloading the tab does not stop the game.** It keeps running in its own process.
* Stop it with `kill $(cat emu/play.pid)`.
* Startup failures land in `emu/play.err`; the cockpit's own log is `emu/play.log`.

## The screen

The picture is drawn on a WebGL2 canvas, so the filters cost nothing worth measuring.

| filter | what it is |
|---|---|
| Pixel (sharp) | nearest-neighbour, every pixel a hard square |
| Smooth | plain bilinear |
| Scanlines | dark line between rows — `strength` |
| CRT (slot mask) | scanlines + shadow mask + glow — `strength`, `curvature`, `glow`, `mask` |
| CRT curved | the same, with the tube bent by default |
| Trinitron (PC-98 monitor) | aperture grille, closer to what this game was played on |
| LCD grid | pixel grid, like a handheld panel |
| Amber monochrome | one-colour phosphor |

`F7` steps through them. **⚙** next to the picker opens the picture settings: the current
filter's sliders, **picture shape** (the PC-98 aspect or square pixels), **integer scaling**
(whole multiples only, so the picture never shimmers) and how the mouse drives the game's
cursor. It opens *over* the page rather than inside it — nothing you open here resizes the
game picture. `F10` is theater mode (everything but the screen dims away), `F11` is real full
screen.

⚠️ The control bar is one row and stays one row. It used to wrap, and a wrapped bar takes its
height out of the picture: at 1366×768 it stood four rows tall, and merely starting autobattle
(the button's label grows) re-wrapped it and resized the game under your hands. Anything that
does not fit is reachable by scrolling the bar sideways.

## Speed and sound

`×1` is the machine's own rate — **56.4 fps, not 60**: that is the PC-98 refresh, and the game
is written around it. `×2` and `×4` multiply it; `MAX` runs as fast as the host manages
(roughly ×16 here). Holding `Tab` gives turbo for as long as you hold it.

**Sound plays at ×1 only**, and is off until you switch it on. Above ×1 the audio would be
chipmunk noise, so it is muted rather than pitched.

⚠️ Key presses are held in *frames*, not milliseconds, so a tap is the same length of game time
at every speed. This is why one tap of a turn key turns you once at ×4, and not three times.

## Controls

| | |
|---|---|
| Arrows, Space, Enter, Esc, letters | go to the game, held exactly as long as you hold them |
| Mouse over the screen | the game's cursor follows it; left / right click are the PC-98 buttons |
| `Tab` (hold) | turbo |
| `F2` / `F4` | snapshot to / from slot 1 |
| `F7` | next screen filter |
| `F8` | screenshot (a PNG of the game screen, not of the page) |
| `F9` or `Pause` | pause |
| `F10` | theater mode |
| `F11` | full screen |
| `F1` | the key list, inside the cockpit |

There are on-screen keys too (arrows, `SPACE`, `ENTER`, `ESC`, `Z`, `X`) — they press and hold
like the real ones, which matters for a game that reads how long a key is down.

**The mouse is absolute.** The cockpit knows where the game's own cursor is (it reads it out of
memory) and drives it to where you point — one mickey per pixel, closing the loop every time.
The mouse-mode picker also offers **capture**, which takes the pointer with Pointer Lock and
feeds relative motion instead; `Esc` gives it back. Use absolute for menus, capture if you
prefer a real mouse feel.

⚠️ Typing into the proofreading editor does not reach the game — the page checks where your
keystrokes are going before it forwards them.

## Instruments

* **HERO** — the name he goes by right now, level, HP (with a bar), EXP, STR, DEF, gold and what
  is equipped, read out of the game's memory rather than off the screen. The name is Astral in
  the first half and Pollux once Fabrice names him in the second (before that, Nameless Man).
* **INVENTORY** — the four item registers the game keeps.
* **MAP** — the floor you are on, drawn from the game's own map data, with where you stand.
  It appears once you are in the dungeon; on the title screen there is nothing to draw.
* **SNAPSHOTS** — save states with a thumbnail. `F2` saves to slot 1, `F4` loads it. There is
  also an automatic snapshot every 5 minutes while you are in the dungeon.
* **LINE ON SCREEN** — one box, the size of the game's own message window (56 columns by four
  rows). It shows the line the game is drawing, and when that line can be rewritten it *is* the
  editor for it — there is no second panel that opens and closes. `⏸` holds the panel on one
  line while the game carries on (it says `held` while it does; the game is never paused by it),
  and `▾` folds it away when you are only playing.
  See [PROOFREADER.md](PROOFREADER.md).
* The four lamps at the top are the emulator process, sound, whether telemetry can read memory,
  and the socket to the cockpit.

⚠️ Telemetry needs the game to be *in play*. On the title screen and after a game over the
player block holds new-game values, and the cockpit says so rather than printing numbers that
mean nothing.

## Autobattle

**Autobattle** walks the floor and fights, so grinding does not cost you an evening. It decides
from memory, never from pixels: it takes the floor's own passability, keeps a list of cells that
led nowhere, waits until the screen is quiet before acting, and checks that the hero actually
moved rather than assuming it did.

It stops by itself and says why:

* your HP fell below the floor you set (`HP ≥` next to the button);
* a menu is holding the input — the keys are moving *its* cursor, not the hero (a save menu,
  a shop, a dialogue). It names the cell so you can see where;
* it has been stuck in one place too long;
* **the moment you touch any control.** That one is deliberate: the cockpit is yours, and
  autobattle yields instantly rather than fighting you for the keyboard.

## Settings

Speed, sound, volume, filter and its sliders are remembered in `emu/play_settings.json`.
Sound is off on a fresh install, on purpose: a cockpit that starts playing music by itself on
someone else's machine is a bug, not a feature.

## Environment

| variable | what it points at | default |
|---|---|---|
| `WW_DISK` | the game image to boot | `game/WordsWorth_play.hdi` |
| `WW_SYSTEM` | the core's system directory (BIOS, fonts) | `emu/system-play` |
| `WW_SAVE` | the core's save directory | `emu/save-play` |
| `WW_PLAY_STATES` | snapshots and their thumbnails | `emu/play_states` |
| `WW_PLAY_SETTINGS` | the settings file | `emu/play_settings.json` |
| `WW_TEXT` | the `text/` pack for the proofreading panel | `text/` next to the repository |
| `WW_PLAY_EDITLOG` | journal of proofreading edits | `emu/play_edits.jsonl` |
| `WW_PLAY_LOG` | the cockpit's log | `emu/play.log` |
| `WW_PLAY_PORT` | the port to listen on | `8778` |
| `WW_PLAY_TRACE` | log every input command and button press | off |

Two cockpits can run at once if you give the second one its own `WW_PLAY_PORT`, `WW_SYSTEM`,
`WW_SAVE` and `WW_PLAY_STATES` — the core writes into its system directory, so sharing one
between two running cockpits corrupts it.

## When something is wrong

| what you see | what it means |
|---|---|
| the screen freezes, the **emu** lamp goes red | the emulator process died. The cockpit notices, says so over the picture, and offers **Restart emulator**; it also restarts an emulator that has stopped answering |
| **mem** lamp dark | telemetry cannot find the game in memory — normal during a cold boot, a problem if it stays dark in the dungeon |
| **link** lamp dark | the page lost the socket; it reconnects by itself |
| the page is blank | look in `emu/play.err`: usually the core is missing, or the venv has no `libretro.py` |
| `no FAT partition found in image` | the file you pointed `WW_DISK` at is not a PC-98 game image |

## Checking it works

```sh
emu/.venv/bin/python emu/play_accept.py --system /path/to/your/np2kai --disk /path/to/your.hdi
```

This is acceptance **by running**, not by unit test: it starts a cockpit of its own on a copy of
your image and checks twenty-one things for real — the page and its files, the socket and actual
frames, every speed against the rate it promises, pause, the mouse, loading a save by clicking
the title menu, telemetry, the message-window panel (including that a bad line is refused and
nothing is written), snapshots, settings, two tabs, autobattle, the map, an emulator killed
mid-game, an emulator that stops answering, and that stopping the cockpit leaves nothing behind.

It never touches a cockpit you already have open, and on failure it saves the screen it was
looking at plus its own traced log, because a screenshot settles arguments that a stack trace
cannot.

The unit tests travel with it:

```sh
emu/.venv/bin/python -m pytest emu/tests/test_play.py emu/tests/test_grind.py emu/tests/test_proofread.py
```

The **page** has a check of its own, because no screenshot can vouch for a layout:

```sh
python3 emu/play_layout_check.py
```

It needs no emulator, no BIOS and no image — a stand-in cockpit speaks the real protocol to the
real page in a headless browser. It drives the page through every state the bottom strip can be
in, at five window sizes, and fails if the game picture changes size, if the control bar wraps
or hides a control off its edge, if the line box changes size, or if the map card changes height
between floors. It needs Chrome or Chromium, and says so rather than failing when there is none.

⚠️ This is the check for a defect class that a running cockpit hides: the page is a flex column,
so anything that grows below the stage takes its height out of the picture. Before it existed,
at 1600×900 the picture took six different sizes while merely playing, and at 1280×720 it
collapsed to under half its width.

## Safety

The cockpit binds `127.0.0.1` and has no authentication of any kind. Anyone who can reach the
port can drive your game, read your saves and — if `text/` is next to it — read and rewrite the
whole script. Do not put it behind a public port, a tunnel or a reverse proxy. If someone else
should use it, they run their own copy on their own machine.
