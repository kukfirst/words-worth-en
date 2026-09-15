#!/bin/bash
# Ночной прогон: от вычитки до приёмки запуском, одной цепочкой.
#
# ⚠️ Каждый шаг пишет свой лог и НЕ продолжает, если предыдущий провалился: полуприменённая
# вычитка хуже непринятой, а собирать образ из непроверенного текста незачем.
#
#   tools/overnight.sh          # ждёт PROOF-DONE и идёт дальше
set -u
cd /home/runekill/development/wordsworth
L=/tmp/ww-night
echo "=== старт $(date +%H:%M) ===" > $L.log

step() {                       # step <имя> <лог> <команда...>
  local name=$1 log=$2; shift 2
  echo "--- $name $(date +%H:%M)" >> $L.log
  if "$@" > "$log" 2>&1; then
    echo "    ✅ $(tail -2 "$log" | head -1)" >> $L.log
    return 0
  fi
  echo "    ❌ провал, см. $log" >> $L.log
  tail -6 "$log" >> $L.log
  return 1
}

# 1. дождаться вычитки
until grep -q PROOF-DONE /tmp/ww-proof3.log 2>/dev/null; do sleep 60; done
echo "вычитка закончена $(date +%H:%M): $(grep 'принято правок' /tmp/ww-proof3.log)" >> $L.log

# 2. вычитка -> скрипты (гейты внутри; не прошла одна строка -- не пишется ничего)
step "импорт вычитки (показать)" $L-import-dry.log timeout 3600 python3 tools/import_text.py
step "импорт вычитки (записать)" $L-import.log timeout 7200 python3 tools/import_text.py --apply \
  || echo "    ⚠️ импорт не применён -- дальше идём на тексте БЕЗ вычитки" >> $L.log

# 3. проверки по исходнику
step "аудит всей батареей" $L-audit.log timeout 3600 python3 tools/audit.py --show 10
step "verify" $L-verify.log timeout 3600 python3 tools/verify.py

# 4. пересборка: QA-образ -> патч -> играбельный
step "QA-образ" $L-qa.log timeout 3600 python3 tools/build_qa_image.py \
  && step "патч" $L-patch.log timeout 3600 python3 tools/make_patch.py \
  && step "играбельный образ" $L-play.log timeout 3600 python3 tools/build_play.py

# 5. приёмка запуском: этажи, сцена Сильваны, магазин
step "приёмка ходьбой" $L-walk.log timeout 20000 bash tools/acceptance.sh

# 6. что реально написано на снятых кадрах
step "чтение кадров" $L-screens.log timeout 3600 python3 tools/screenqa.py --since 6h

echo "=== готово $(date +%H:%M) ===" >> $L.log
echo NIGHT-DONE >> $L.log
