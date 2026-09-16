.PHONY: help juice verify verify-full selftest check e2e patch release play accept clean

help:
	@echo 'Words Worth - English translation, build targets'
	@echo
	@echo '  make juice       fetch the AI5 (de)compiler at its pinned commit (not vendored)'
	@echo '  make verify      layout, names, sizes, patch integrity, image build  (seconds)'
	@echo '  make verify-full ...plus a full recompile and an emulator run        (~1 hour)'
	@echo '  make selftest    break each defect class on a copy, prove the checks catch it'
	@echo
	@echo 'To play (see emu/COCKPIT.md - needs the np2kai core, a PC-98 BIOS and your image):'
	@echo '  make play        start the cockpit at http://127.0.0.1:8778/'
	@echo '  make accept      check the cockpit on your machine by running it'
	@echo
	@echo 'For proofreaders (see PROOFREADING.md - no game or build tools needed):'
	@echo '  make check       check your edits in text/ - writes nothing'
	@echo '  make patch       rebuild dist/patch from the QA image'
	@echo '  make release     the whole chain: names, layout, scripts, images, patch, checks'
	@echo
	@echo 'You need your own copy of the game. Nothing in this repository contains it.'
	@echo 'Put the Japanese hard-disk image at game/WordsWorth.hdi first.'

juice:
	tools/get_juice.sh

verify:
	python3 tools/verify.py

# A check nobody has ever seen fail is indistinguishable from no check at all.
# This breaks one defect of each class on a copy of en/ and requires the matching
# step to name it.
selftest:
	python3 tools/selftest.py

# The one command a proofreader runs. Reads text/, writes nothing.
check:
	python3 tools/check.py

# The play cockpit. WW_DISK is your own patched image, WW_SYSTEM your own PC-98 BIOS
# directory -- neither ships here. Details and defaults: emu/COCKPIT.md
play:
	emu/play.sh

# Acceptance by running: starts a cockpit of its own on a copy of your image.
accept:
	emu/.venv/bin/python emu/play_accept.py \
	  --system $${WW_SYSTEM:?set WW_SYSTEM to your np2kai BIOS directory} \
	  --disk $${WW_DISK:?set WW_DISK to your patched image}

# Walks the whole proofreading loop on throwaway copies: pack -> clean clone -> edits ->
# checker -> import. Proves the loop works for someone with nothing installed.
e2e:
	python3 tools/e2e_proofread.py

verify-full:
	python3 tools/verify.py --full

patch:
	python3 tools/make_patch.py

release:
	python3 tools/release.py

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
