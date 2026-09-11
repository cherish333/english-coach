#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL="http://127.0.0.1:8765"
LOG_FILE="$DIR/data/logs/server.log"

mkdir -p "$DIR/data/logs"

# 1. Check if backend server is already running and responsive
if ! curl -fsS --max-time 1 "$URL/api/progress" >/dev/null 2>&1; then
    # Start silently in the background (0 terminal window)
    if systemctl --user start english-coach.service >/dev/null 2>&1; then
        :
    else
        cd "$DIR"
        if [ -x "$DIR/.venv/bin/python" ]; then
            nohup "$DIR/.venv/bin/python" -m src.server >> "$LOG_FILE" 2>&1 &
        else
            nohup python3 -m src.server >> "$LOG_FILE" 2>&1 &
        fi
    fi

    # Wait for server to become ready (up to 15s)
    for i in $(seq 1 30); do
        if curl -fsS --max-time 1 "$URL/api/progress" >/dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done
fi

# 2. Launch the desktop web app without any command prompt or terminal window
if command -v omarchy-launch-webapp >/dev/null 2>&1; then
    exec omarchy-launch-webapp "$URL"
elif command -v chromium >/dev/null 2>&1; then
    exec chromium --app="$URL"
else
    exec xdg-open "$URL"
fi
