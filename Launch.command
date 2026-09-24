#!/bin/bash
# Launch.command — macOS equivalent of Launch.vbs. Double-click it in Finder.
#
# Starts the backend (uvicorn :8000) and frontend (Vite :5173), waits for both
# ports, then opens the browser — the same arrangement Start-App.ps1 produces on
# Windows. Unlike Windows there is no separate Setup step: the first run creates
# the venv and installs packages, then launches.
#
# Double-click Stop.command to shut it down.
set -u

cd "$(dirname "$0")" || exit 1
ROOT="$PWD"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
LOGS="$ROOT/.logs"
mkdir -p "$LOGS"

# A Finder-launched script does not read your shell profile, so its PATH can miss
# Homebrew entirely. Same class of problem Start-App.ps1 solves by re-reading PATH
# from the registry (spec 12.1).
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

die() {
    echo "" >&2
    echo "ERROR: $1" >&2
    osascript -e "display dialog \"$1\" with title \"PBI Test Utility\" buttons {\"OK\"} with icon stop" >/dev/null 2>&1
    exit 1
}

# curl is used rather than a raw socket because `localhost` resolves to both ::1
# (which Vite binds) and 127.0.0.1 (which uvicorn binds) — checking only one
# family is the false-timeout bug recorded in spec 12.1.
port_up() { curl -s -o /dev/null -m 1 "http://localhost:$1" 2>/dev/null; }

if port_up 8000 && port_up 5173; then
    echo "Already running — opening the browser."
    open "http://localhost:5173"
    exit 0
fi

command -v python3 >/dev/null 2>&1 || die "Python 3 not found. Install it from python.org, or run: brew install python"
command -v node    >/dev/null 2>&1 || die "Node.js not found. Install it from nodejs.org, or run: brew install node"

# ── first-run setup (this is Setup.bat, folded in) ───────────────────────────
if [ ! -x "$BACKEND/.venv/bin/uvicorn" ]; then
    echo "First run — creating the Python environment. This takes a minute or two..."
    python3 -m venv "$BACKEND/.venv" || die "Could not create the virtualenv in backend/.venv"
    "$BACKEND/.venv/bin/pip" install --quiet --upgrade pip
    "$BACKEND/.venv/bin/pip" install -r "$BACKEND/requirements.txt" \
        || die "pip install failed. See the Terminal window for the reason."
fi

if [ ! -d "$FRONTEND/node_modules" ]; then
    echo "First run — installing frontend packages..."
    ( cd "$FRONTEND" && npm install ) \
        || die "npm install failed. See the Terminal window for the reason."
fi

# ── start both servers ───────────────────────────────────────────────────────
# Backend must start from inside backend/ — the imports are absolute.
# --reload restarts it when a .py file changes, so an edit needs no relaunch. Only
# *.py triggers it (uvicorn's FileFilter default), so a Settings save or a history
# write will not restart the server mid-run. PBI_NO_RELOAD=1 turns it off.
echo "Starting backend..."
RELOAD="--reload"
[ "${PBI_NO_RELOAD:-}" = "1" ] && RELOAD=""
( cd "$BACKEND" && nohup .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 $RELOAD \
    >"$LOGS/backend.log" 2>&1 & echo $! >"$LOGS/backend.pid" )

echo "Starting frontend..."
VITE="$FRONTEND/node_modules/vite/bin/vite.js"
if [ -f "$VITE" ]; then
    # Straight to node, skipping the npm wrapper — worth ~1s, same trick as Windows.
    ( cd "$FRONTEND" && nohup node "$VITE" >"$LOGS/frontend.log" 2>&1 & echo $! >"$LOGS/frontend.pid" )
else
    ( cd "$FRONTEND" && nohup npm run dev >"$LOGS/frontend.log" 2>&1 & echo $! >"$LOGS/frontend.pid" )
fi

# Poll both together at 150ms, so the wait is the slower server and not the sum.
printf "Waiting for servers"
READY=""
i=0
while [ $i -lt 300 ]; do            # 300 x 0.15s = 45s, same budget as Windows
    if port_up 8000 && port_up 5173; then READY=1; break; fi
    [ $((i % 7)) -eq 0 ] && printf "."
    sleep 0.15
    i=$((i + 1))
done
echo ""

[ -n "$READY" ] || die "The servers did not come up within 45 seconds. Check .logs/backend.log and .logs/frontend.log."

open "http://localhost:5173"
echo ""
echo "PBI Test Utility is running — http://localhost:5173"
echo "Logs: .logs/  ·  To stop: double-click Stop.command"
