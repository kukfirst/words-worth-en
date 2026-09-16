#!/bin/bash
# Acceptance by RUNNING: walk through every cut floor, through the Sylvana scene, and through
# the shops.
#
# ⚠️ The verdict is read OFF THE SCREEN (`emu/goto.py` in WW_SEQ mode), not from memory: on
# exiting to DOS the game doesn't clear memory, and `state.identify` keeps reporting the scene
# as resident for a long while after. One crash slipped through exactly that way (§30).
#
# Teleport: the scene name sits at the start of the save slot (`FLAG0`) as an ASCII string, so
# any room can be reached without playing through the story.
set -u
cd "$(dirname "$0")/.."
OUT=/tmp/ww-acc
rm -rf $OUT; mkdir -p $OUT
: > $OUT/result.txt

go() {                      # go <scene> <event|-> <sequence>
  # ⚠️ `-` instead of an event -- a scene without a floor map (shop, yard, storeroom): there
  # `goto.py` exits without starting, because `state.floormap` is empty. `emu/visit.py` walks
  # those instead.
  local scene=$1 ev=$2 seq=$3 low
  low=$(echo "$scene" | tr 'A-Z' 'a-z')
  cp game/WordsWorth_play.hdi $OUT/$scene.hdi
  python3 - "$low.mes" "$OUT/$scene.hdi" <<'PY'
import sys,os,subprocess,tempfile,pathlib
sys.path.insert(0,'tools'); import hdimage
scene,img=sys.argv[1],pathlib.Path(sys.argv[2])
d=pathlib.Path(tempfile.mkdtemp()); cfg=d/'m'; hdimage.mtoolsrc(img,cfg)
env=os.environ|{'MTOOLSRC':str(cfg),'MTOOLS_SKIP_CHECK':'1'}
f=d/'F0'; subprocess.run(['mcopy','-o','z:/WW/FLAG0',str(f)],env=env,capture_output=True)
b=bytearray(f.read_bytes()); b[:len(scene)+1]=scene.encode()+b'\0'
g=d/'t'; g.write_bytes(bytes(b))
subprocess.run(['mcopy','-o',str(g),'z:/WW/FLAG0'],env=env,capture_output=True)
PY
  rm -rf $OUT/s_$scene; mkdir -p $OUT/s_$scene; cp -r emu/system $OUT/s_$scene/system
  if [ "$ev" = "-" ]; then
    WW_SYSTEM=$OUT/s_$scene/system WW_SCRATCH=$OUT/s_$scene WW_SEQ="$seq" \
      timeout 2400 emu/.venv/bin/python emu/visit.py $OUT/$scene.hdi > $OUT/$scene.log 2>&1
  else
    WW_SYSTEM=$OUT/s_$scene/system WW_SCRATCH=$OUT/s_$scene WW_SEQ="$seq" \
      timeout 2400 emu/.venv/bin/python emu/goto.py $OUT/$scene.hdi --event "$ev" \
      > $OUT/$scene.log 2>&1
  fi
  local alive dead lines
  alive=$(grep -cE "✅ (sequence passed|scene passed)" $OUT/$scene.log)
  dead=$(grep -c "EXITED TO DOS" $OUT/$scene.log)
  lines=$(grep -oE "distinct lines shown: [0-9]+" $OUT/$scene.log | grep -oE "[0-9]+$")
  local note=""
  if [ "$dead" != "0" ]; then
    # ⚠️ This is a CONTROL check, not output. A scene can simply be unreachable by teleport:
    # `SHP_4S` dies the same way on the UNMODIFIED Japanese image, down to the sign. Without
    # this check that death reads as our defect and costs an hour of searching
    # (2026-09-15, caught exactly like this).
    if control "$low" "$seq"; then
      note=" ⚠️ but the Japanese original dies here too -- scene unreachable by teleport"
      dead=0; alive=1
    else
      note=" ❗ ALIVE on the Japanese original -- this is our defect"
    fi
  fi
  printf '%-10s alive=%s crashes=%s replicas=%s%s\n' "$scene" "$alive" "$dead" "${lines:-?}" "$note" >> $OUT/result.txt
  rm -f $OUT/$scene.hdi
}

control() {                 # control <scene-lowercase> <sequence> -> 0 if the JAPANESE one also died
  local low=$1 seq=$2 base=game/base/WordsWorth_neokobe.hdi
  cp $base $OUT/ctl.hdi
  python3 - "$low.mes" "$OUT/ctl.hdi" <<'PY'
import sys,os,subprocess,tempfile,pathlib
sys.path.insert(0,'tools'); import hdimage
scene,img=sys.argv[1],pathlib.Path(sys.argv[2])
d=pathlib.Path(tempfile.mkdtemp()); cfg=d/'m'; hdimage.mtoolsrc(img,cfg)
env=os.environ|{'MTOOLSRC':str(cfg),'MTOOLS_SKIP_CHECK':'1'}
f=d/'F0'; subprocess.run(['mcopy','-o','z:/WW/FLAG0',str(f)],env=env,capture_output=True)
b=bytearray(f.read_bytes()); b[:len(scene)+1]=scene.encode()+b'\0'
g=d/'t'; g.write_bytes(bytes(b))
subprocess.run(['mcopy','-o',str(g),'z:/WW/FLAG0'],env=env,capture_output=True)
PY
  rm -rf $OUT/ctl; mkdir -p $OUT/ctl; cp -r emu/system $OUT/ctl/system
  WW_SYSTEM=$OUT/ctl/system WW_SCRATCH=$OUT/ctl WW_SEQ="$seq" \
    timeout 2400 emu/.venv/bin/python emu/visit.py $OUT/ctl.hdi > $OUT/ctl_$low.log 2>&1
  rm -f $OUT/ctl.hdi
  grep -q "EXITED TO DOS" $OUT/ctl_$low.log
}

WALK="up,return_key*8,left,up,return_key*8,left,up,return_key*8,up,return_key*10"

# Every cut floor: loads, is walkable, events respond.
for f in FLOOR00 FLOOR01 FLOOR02 FLOOR03 FLOOR04 FLOOR05 FLOOR08 FLOOR09 FLOOR10 FLOOR11 FLOOR5A; do
  go $f 1 "$WALK"
done

# The Sylvana scene -- the very same §30 crash: 62 return, up, 8 more.
go FLOOR02 2 "return_key*62,up,return_key*8"

# Shops: the money-related lines are assembled from halves (`shopfix.py`), and those were
# exactly what broke. The purchase menu is walked up-down with confirmation, not a bare `return`.
# ⚠️ The purchase menu is drawn OUTSIDE the message window, and its keys couldn't be worked out
# blindly: any sequence just cycles "what will you buy?". So this checks what CAN be checked --
# the scene loads and doesn't drop to DOS. The money lines themselves are guarded by
# `tools/audit.py` against the source.
SHOP="return_key*4,down,return_key*3,up,return_key*3,escape,return_key*4,space*3"
for s in SHP_0A SHP_0B SHP_0I SHP_5I SHP_4S SHP_2I; do
  go $s - "$SHOP"
done

# Inn and storeroom: lines assembled from halves there too.
go YADO - "return_key*8,down,return_key*6,escape,return_key*4"
go KANKIN - "return_key*8,down,return_key*6,escape,return_key*4"

# ⚠️ The tally is computed BEFORE appending it, otherwise awk matches its own line and
# acceptance "fails" for no reason (caught 2026-09-15: rc=1 with 20/20 scenes alive).
bad=$(awk '$2=="alive=0" || $3!="crashes=0" {print "❌ "$0}' $OUT/result.txt)
{ echo "--- summary ---"; [ -n "$bad" ] && echo "$bad" || echo "✅ all scenes alive"; } >> $OUT/result.txt
cat $OUT/result.txt

# Frames -- into the shared pool, screenqa will read them
mkdir -p emu/audit
n=0
for d in $OUT/s_*/frames; do
  [ -d "$d" ] || continue
  tag=$(basename "$(dirname "$d")")
  for f in "$d"/*.png; do [ -e "$f" ] || continue; cp "$f" "emu/audit/${tag}_$(basename "$f")"; n=$((n+1)); done
done
echo "frames collected: $n"
[ -n "$bad" ] && exit 1
exit 0
