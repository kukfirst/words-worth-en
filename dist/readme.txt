Words Worth (elf, PC-98, 1993) - English translation
V 1.0 - 13/09/26

Words Worth is elf's first-person dungeon RPG from 1993, set in a world split between the
Light Clan and the Shadow Clan, each convinced the other has been lying about the same stone
tablet for a thousand years. You play Astral, a Shadow Clan swordsman who goes up the tower
to find out which half of the truth is missing. It is best known today for the OVA it spawned,
which is a shame, because the game underneath is a compact, mean little dungeon crawler with
a story that actually goes somewhere.

The PC-98 original has never been playable in English. What exists is a 2005 fan patch for
the 1999 Windows remake - a different game in practice, with redrawn characters, reworked
gameplay and polygon dungeons - and that patch is itself partial, covering the story dialogue.
The 1993 version, the one with the original art, has stayed Japanese for thirty-three years.

This patch puts its whole script into English: dialogue, battle messages, shops, item and
equipment names, the save/load menu and the title screen.

CONTENT WARNING
Words Worth is an adult game. It contains explicit sex scenes, some of them non-consensual,
and the usual early-90s attitudes that come with that. Everything is translated plainly -
nothing is censored or softened. Don't play it if that is not for you, and don't play it if
you are under 18.

HOW TO
Apply the xdelta patch to the Japanese hard-disk image:

    Words Worth.hdi     CRC32 AE44FCDD     20,955,136 bytes
                        MD5   3b240225ff7cd955f8fd9515b41227f2

This is the file as it ships in the widely circulated PC-98 collection. On Windows, drop the
image and the patch onto xdelta UI. On Linux or macOS:

    xdelta3 -d -s "Words Worth.hdi" words-worth-en-v1-0.xdelta "Words Worth (EN).hdi"

The result is:

    Words Worth (EN).hdi  CRC32 A3F44833  MD5 549833dce7d39fe81c3e9dbc1e5bf06f

Patch a FRESH image, not one you have played. The five save slots carry the hero's name, which
the engine substitutes into dialogue at runtime, so the patch has to rewrite those fields -
otherwise English lines come out with katakana names in them. As a side effect the patched
image starts clean: the collection's copy ships with somebody else's save sitting in slot 1,
and this patch replaces it with an empty one.

The game runs on Neko Project II kai (np2kai), standalone or as the libretro core in
RetroArch, Batocera, ROCKNIX and the rest. Pick "Load 1" on the title screen to start - the
"New Game" entry does nothing on this disk image, which is true of the untouched Japanese
original as well, not something this patch broke. Loading the empty slot 1 starts a new game.

WHAT IS AND IS NOT TRANSLATED
- 95 script files, 22,010 text forms - all of them in English.
- The kana rows on the name-entry keyboard are left alone on purpose: that screen is how you
  type a name, and it offers a Latin keyboard right next to them.
- Equipment reads DAGGAR, BASTERD, PATTD ARMOR and so on. Those are elf's own romanisations
  in the Japanese original, printed by the game itself, not translation errors. They are left
  exactly as the authors wrote them.

KNOWN ISSUES
- Battle lines are assembled at runtime from the enemy's name plus a sentence fragment, and
  the engine wraps at the edge of the window without caring where a word ends. Against
  long-named enemies a line can still break mid-word. The text is readable, it just looks
  untidy. This is the known defect for v1.1.
- Line breaks are laid out for a six-character hero name (the defaults are Astral and Pollux).
  A much longer name shifts the wrapping.

ABOUT THE TRANSLATION
Play a few screens before you read the next section, because the text is the argument and the
method is just trivia. The jokes land. Characters keep their own voices - the shopkeepers
haggle like shopkeepers, Kaiser boasts like Kaiser, the guards grumble. Nothing reads like
those word-salad half-sentences that give a machine translation away in the first five
minutes, because sentences like that were caught and rejected rather than shipped.

I am not going to pretend about where it came from, so:

HOW IT WAS MADE
The script was translated by a large language model running locally - Qwen3.8-27B on
llama.cpp, on my own machine, nothing sent anywhere - but the model was never in charge of
anything. It only ever proposes a translation of a batch of lines. Every accept and reject is
made by code: gates that check line count, character set, glossary terms, name markers, text
box width, and - after recompiling the script - that the rebuilt bytecode has exactly the same
instruction skeleton as the Japanese original. A batch that fails a gate is retried, then cut
in half and retried; whatever still fails keeps its Japanese instead of shipping an invention.
Nothing reached this patch because a model was confident about it.

The layout is simulated rather than eyeballed: a model of elf's compiler and of the engine's
message window reproduces what every line will look like on screen, character for character,
and that simulation is checked against real captured emulator frames. That is how 898 broken
words and 605 badly wrapped lines were found by counting instead of by playing, and it is why
the text sits in the box properly.

The build is verified by running it: the patched image is booted in an emulator, walked until
a random encounter happens and made to survive the fight, because compiling is necessary and
nowhere near sufficient.

Judge it by the script. If a line reads wrong to you, post it - I would rather fix it than
argue about the method.

CREDITS
Translation pipeline, hacking, tooling: kukfirst
Decompiler and compiler for elf's AI5 .MES bytecode: juice, by Kyungdahm Yun (tomyun) -
  https://github.com/tomyun/juice
Emulator used for building and verification: Neko Project II kai (np2kai), by AZO234
