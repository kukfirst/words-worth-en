# Proofreading Words Worth

Thank you for doing this. Here is everything you need, and nothing you don't.

You need **Python 3.9 or newer** and a text editor. That is all. You do not need the game,
an emulator, Racket, or any of the build tooling — proofreading is editing text, not building
a patch.

---

## Setup, once

```sh
git clone https://github.com/kukfirst/words-worth-en
cd words-worth-en
```

Then unzip the `text/` folder you were sent **into this folder**, so that it sits next to
`tools/`:

```
words-worth-en/
├── text/          ← the script, from the zip
├── tools/
└── README.md
```

⚠️ `text/` is deliberately not in the repository. It is the game's full script, and this
scene distributes patches rather than scripts. Please keep it off public places.

## Every time you sit down

```sh
git pull          # pick up fixes and any tooling changes
```

Then edit, then:

```sh
python3 tools/check.py
```

That is the whole loop. `check.py` writes nothing — it reads what you edited and tells you
what the game would refuse.

---

## What you are editing

`text/` holds one JSON file per script file. One entry per line of dialogue:

```json
{
 "id": "FLOOR02.MES#41",
 "en": "[{0}]: My, my head's spinning... Someone cast a\nspell on me.",
 "was": "fd514727",
 "marks": ["0"],
 "col": 0,
 "screen": ["[xxxxxx]: My, my head's spinning... Someone cast a",
            "spell on me."]
}
```

**Change `en`. Leave everything else alone.**

| field | what it is |
|---|---|
| `en` | the line as it will appear in the game — this is the one you edit |
| `screen` | how it will actually land in the game's 56-column window, line by line |
| `id` | which script file and which line — quote this if you want to ask about one |
| `was`, `marks`, `col` | machinery the checker uses; changing them breaks the check |

`screen` is produced by a model of the game's own compiler and message window, checked against
real captured frames. If a line looks wrong there, it will look wrong in the game. `xxxxxx`
stands for the hero's name, which the player types and the engine substitutes at runtime.

---

## The five rules

**1. `{0}` and `{1}` must survive exactly.** The engine prints the hero's or an enemy's name
there. `{0}` at the very start or very end of a line is a join point — it cannot move into
the middle of the sentence, because the name is printed by a separate instruction.

**2. Only characters the game can draw.** 208 glyphs. No curly quotes (`“” ‘’`), no em-dash
(`—`), no accents, no backslash, no tilde. Plain ASCII apostrophes and quotes.

**3. Mind the window.** 56 columns. The checker will tell you if a line is too wide, breaks a
word in half, or leaves one word alone on a line. Watch `col` — when it is not `0`, the
engine has already printed something (usually a name) before your line starts, and you have
that much less room on the first line.

**4. Keep the `[Name]:` tag.** It is who is speaking, and it must stay, spelled exactly the
same, with the same capital letter. `check.py` refuses a line whose tag was renamed, re-cased
or dropped — including a one-letter slip like `[Innkeper]` — and so does the editor inside the
game. Fix the English after the tag, not the tag.

**5. The game is adult and blunt.** Explicit scenes, crude jokes, characters being unpleasant.
That is the original — do not soften it. Fix the English, not the content.

---

## What to look for

The script was translated by a machine and passed through one proofreading round, so the
obvious wreckage is gone. What is left is the kind of thing only a reader catches:

- **Missing nouns after a group name.** Japanese does not need one, English does.
  `took out some Light Clan` → `...some Light Clan members`.
- **Literal word order** that is grammatical but nobody would say.
  `you're all children before my talent` → `next to my talent you're both children`.
- **Wrong register.** A shopkeeper haggling like a narrator, a thug speaking politely.
- **Inconsistent address.** The same character called both `you` and by name in one scene.
- **Dead idioms** translated word for word.

Things that are **not** errors, please leave them:

- `DAGGAR`, `BASTERD`, `PATTD ARMOR` — elf's own romanisations, printed by the Japanese
  original. They are in the game, not in the translation.
- The kana rows on the name-entry screen. That screen is how you type a name.

---

## If you would rather read the lines in the game

Optional, and a different kind of work: you can play the patched game in the cockpit that ships
with this repository and fix a line **while looking at it on screen**, in its own context, with
the same checks running before anything is written. That needs the game, an emulator core and a
PC-98 BIOS of your own — everything the plain path above deliberately does not.

**[→ emu/PROOFREADER.md](emu/PROOFREADER.md)** for the panel, **[emu/COCKPIT.md](emu/COCKPIT.md)**
for installing and running the cockpit itself.

Both paths write to the same place: your `text/*.json`. You can use either, or both.

## Sending it back

Run the check until it says this:

```
✓ all 42 edits in 7 files pass. Send the text/ folder back.
```

Then zip `text/` and send it. On our side it goes through the same checks plus a recompile,
and **if any single line fails, nothing at all is written** — half an accepted proofread is
worse than none, because afterwards nobody can tell what landed.

## If something is wrong with the tooling

Open an issue on the repository, or just say so. `check.py` refusing a line it should accept
is a bug in `check.py`, and it has been one before.
