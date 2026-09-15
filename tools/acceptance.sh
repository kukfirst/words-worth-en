#!/bin/bash
# Приёмка ЗАПУСКОМ: пройти по всем разрезанным этажам, по сцене Сильваны и по лавкам.
#
# ⚠️ Вердикт снимается ПО ЭКРАНУ (`emu/goto.py` в режиме WW_SEQ), а не по памяти: выходя в
# DOS, игра память не чистит, и `state.identify` ещё долго рапортует сцену резидентной.
# Один вылет так и пропустили (§30).
#
# Телепорт: имя сцены лежит в начале слота сохранения (`FLAG0`) ASCII-строкой, поэтому
# попасть в любую комнату можно без прохождения сюжета.
set -u
cd /home/runekill/development/wordsworth
OUT=/tmp/ww-acc
rm -rf $OUT; mkdir -p $OUT
: > $OUT/result.txt

go() {                      # go <сцена> <событие|-> <последовательность>
  # ⚠️ `-` вместо события -- сцена без карты этажа (лавка, двор, кладовая): там `goto.py`
  # выходит, не начав, потому что `state.floormap` пуст. Такие проходит `emu/visit.py`.
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
  alive=$(grep -cE "✅ (последовательность пройдена|сцена пройдена)" $OUT/$scene.log)
  dead=$(grep -c "ВЫШЛА В DOS" $OUT/$scene.log)
  lines=$(grep -oE "разных реплик показано: [0-9]+" $OUT/$scene.log | grep -oE "[0-9]+$")
  local note=""
  if [ "$dead" != "0" ]; then
    # ⚠️ КОНТРОЛЬ, а не вывод. Сцена может быть просто недостижима телепортом: `SHP_4S`
    # умирает и на НЕТРОНУТОМ японском образе, знак в знак. Без этой проверки такая смерть
    # читается как наш дефект и стоит часа поисков (2026-09-15, поймано ровно так).
    if control "$low" "$seq"; then
      note=" ⚠️ но и японский оригинал умирает тут же -- сцена недостижима телепортом"
      dead=0; alive=1
    else
      note=" ❗ на японском оригинале ЖИВА -- это наш дефект"
    fi
  fi
  printf '%-10s жив=%s вылетов=%s реплик=%s%s\n' "$scene" "$alive" "$dead" "${lines:-?}" "$note" >> $OUT/result.txt
  rm -f $OUT/$scene.hdi
}

control() {                 # control <сцена-строчными> <последовательность> -> 0, если ЯПОНСКИЙ тоже умер
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
  grep -q "ВЫШЛА В DOS" $OUT/ctl_$low.log
}

WALK="up,return_key*8,left,up,return_key*8,left,up,return_key*8,up,return_key*10"

# Все разрезанные этажи: грузится, ходится, события отвечают.
for f in FLOOR00 FLOOR01 FLOOR02 FLOOR03 FLOOR04 FLOOR05 FLOOR08 FLOOR09 FLOOR10 FLOOR11 FLOOR5A; do
  go $f 1 "$WALK"
done

# Сцена Сильваны -- тот самый вылет §30: 62 return, up, ещё 8.
go FLOOR02 2 "return_key*62,up,return_key*8"

# Лавки: реплики о деньгах собираются из половин (`shopfix.py`), и ломались именно они.
# Меню покупки проходится вверх-вниз с подтверждением, а не одним `return`.
# ⚠️ Меню покупки рисуется ВНЕ окна сообщения, и подобрать его клавиши вслепую не вышло:
# любая последовательность крутит «what will you buy?». Поэтому здесь проверяется то, что
# проверить можно, -- сцена грузится и не выходит в DOS. Сами реплики о деньгах стережёт
# `tools/audit.py` по исходнику.
SHOP="return_key*4,down,return_key*3,up,return_key*3,escape,return_key*4,space*3"
for s in SHP_0A SHP_0B SHP_0I SHP_5I SHP_4S SHP_2I; do
  go $s - "$SHOP"
done

# Постоялый двор и кладовая: там же собранные из половин реплики.
go YADO - "return_key*8,down,return_key*6,escape,return_key*4"
go KANKIN - "return_key*8,down,return_key*6,escape,return_key*4"

# ⚠️ Итог считается ДО дописывания, иначе awk матчит собственную строку и приёмка
# «проваливается» на пустом месте (поймано 2026-09-15: rc=1 при 20 живых сценах из 20).
bad=$(awk '$2=="жив=0" || $3!="вылетов=0" {print "❌ "$0}' $OUT/result.txt)
{ echo "--- итог ---"; [ -n "$bad" ] && echo "$bad" || echo "✅ все сцены живы"; } >> $OUT/result.txt
cat $OUT/result.txt

# Кадры -- в общий котёл, их прочитает screenqa
mkdir -p emu/audit
n=0
for d in $OUT/s_*/frames; do
  [ -d "$d" ] || continue
  tag=$(basename "$(dirname "$d")")
  for f in "$d"/*.png; do [ -e "$f" ] || continue; cp "$f" "emu/audit/${tag}_$(basename "$f")"; n=$((n+1)); done
done
echo "кадров собрано: $n"
[ -n "$bad" ] && exit 1
exit 0
