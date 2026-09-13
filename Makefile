.PHONY: help juice verify verify-full patch release clean

help:
	@echo 'Words Worth - English translation, build targets'
	@echo
	@echo '  make juice       fetch the AI5 (de)compiler at its pinned commit (not vendored)'
	@echo '  make verify      layout, names, sizes, patch integrity, image build  (seconds)'
	@echo '  make verify-full ...plus a full recompile and an emulator run        (~1 hour)'
	@echo '  make patch       rebuild dist/patch from the QA image'
	@echo '  make release     the whole chain: names, layout, scripts, images, patch, checks'
	@echo
	@echo 'You need your own copy of the game. Nothing in this repository contains it.'
	@echo 'Put the Japanese hard-disk image at game/WordsWorth.hdi first.'

juice:
	tools/get_juice.sh

verify:
	python3 tools/verify.py

verify-full:
	python3 tools/verify.py --full

patch:
	python3 tools/make_patch.py

release:
	python3 tools/release.py

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
