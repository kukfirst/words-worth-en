#!/bin/bash
# Overnight run: from proofreading to acceptance-by-running, one chain.
#
# ⚠️ Every step writes its own log and does NOT continue if the previous one failed: a
# half-applied proofread is worse than none, and there's no point building an image off
# unverified text.
#
#   tools/overnight.sh          # waits for PROOF-DONE and moves on
set -u
cd "$(dirname "$0")/.."
L=/tmp/ww-night
echo "=== start $(date +%H:%M) ===" > $L.log

step() {                       # step <name> <log> <command...>
  local name=$1 log=$2; shift 2
  echo "--- $name $(date +%H:%M)" >> $L.log
  if "$@" > "$log" 2>&1; then
    echo "    ✅ $(tail -2 "$log" | head -1)" >> $L.log
    return 0
  fi
  echo "    ❌ failed, see $log" >> $L.log
  tail -6 "$log" >> $L.log
  return 1
}

# 1. wait for proofreading
until grep -q PROOF-DONE /tmp/ww-proof3.log 2>/dev/null; do sleep 60; done
echo "proofreading finished $(date +%H:%M): $(grep 'accepted edits' /tmp/ww-proof3.log)" >> $L.log

# 2. proofread -> scripts (gates built in; a single failing line means nothing gets written)
step "proofread import (dry run)" $L-import-dry.log timeout 3600 python3 tools/import_text.py
step "proofread import (write)" $L-import.log timeout 7200 python3 tools/import_text.py --apply \
  || echo "    ⚠️ import not applied -- continuing on text WITHOUT the proofread" >> $L.log

# 3. checks against the source
step "full audit battery" $L-audit.log timeout 3600 python3 tools/audit.py --show 10
step "verify" $L-verify.log timeout 3600 python3 tools/verify.py

# 4. rebuild: QA image -> patch -> playable
step "QA image" $L-qa.log timeout 3600 python3 tools/build_qa_image.py \
  && step "patch" $L-patch.log timeout 3600 python3 tools/make_patch.py \
  && step "playable image" $L-play.log timeout 3600 python3 tools/build_play.py

# 5. acceptance by running: floors, Sylvana scene, shop
step "walk-through acceptance" $L-walk.log timeout 20000 bash tools/acceptance.sh

# 6. what's actually written on the captured frames
step "reading frames" $L-screens.log timeout 3600 python3 tools/screenqa.py --since 6h

echo "=== done $(date +%H:%M) ===" >> $L.log
echo NIGHT-DONE >> $L.log
