#!/bin/sh
# Start the Words Worth play cockpit if it is not running, then open it in the browser.
# The game keeps running when the tab is closed or reloaded; stop it with `kill $(cat emu/play.pid)`.
cd "$(dirname "$0")" || exit 1
PORT=${WW_PLAY_PORT:-8778}
URL="http://127.0.0.1:$PORT/"
if ! curl -fs -o /dev/null "${URL}state.json"; then
    nohup ./.venv/bin/python play.py >/dev/null 2>>play.err &
    i=0
    while [ $i -lt 50 ] && ! curl -fs -o /dev/null "${URL}state.json"; do
        sleep 0.2
        i=$((i + 1))
    done
fi
xdg-open "$URL" >/dev/null 2>&1 &
