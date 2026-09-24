#!/bin/bash
# Stop.command — macOS equivalent of Stop-App.vbs. Double-click it in Finder.
set -u

cd "$(dirname "$0")" || exit 1
LOGS="$PWD/.logs"

# The PIDs Launch.command recorded.
for f in "$LOGS/backend.pid" "$LOGS/frontend.pid"; do
    if [ -f "$f" ]; then
        kill "$(cat "$f")" 2>/dev/null
        rm -f "$f"
    fi
done

# Fallback: anything still holding the ports, including servers someone started
# by hand in a terminal. lsof is part of macOS, no install needed.
for port in 8000 5173; do
    pids=$(lsof -ti "tcp:$port" 2>/dev/null)
    [ -n "$pids" ] && kill $pids 2>/dev/null
done

sleep 1
still=""
for port in 8000 5173; do
    [ -n "$(lsof -ti "tcp:$port" 2>/dev/null)" ] && still="$still $port"
done

if [ -n "$still" ]; then
    echo "Still listening on:$still — run: lsof -ti tcp:8000 | xargs kill -9"
else
    echo "Servers stopped."
fi
