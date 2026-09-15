.PHONY: help juice verify verify-full selftest check e2e patch release clean

help:
	@echo 'Words Worth - English translation, build targets'
	@echo
	@echo '  make juice       fetch the AI5 (de)compiler at its pinned commit (not vendored)'
	@echo '  make verify      layout, names, sizes, patch integrity, image build  (seconds)'
	@echo '  make verify-full ...plus a full recompile and an emulator run        (~1 hour)'
	@echo '  make selftest    break each defect class on a copy, prove the checks catch it'
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
