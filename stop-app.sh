#!/usr/bin/env bash

# 1. Stop systemd user service if active
if systemctl --user is-active --quiet english-coach.service 2>/dev/null; then
    systemctl --user stop english-coach.service 2>/dev/null || true
fi

# 2. Terminate any stray python processes running src.server or listening on port 8765
pkill -f "python.*src\.server" 2>/dev/null || true
fuser -k 8765/tcp 2>/dev/null || true

# 3. Close the Chromium app window for English Coach if open
if command -v hyprctl >/dev/null 2>&1; then
    pids=$(hyprctl clients -j 2>/dev/null | jq -r '.[] | select((.title | test("English Coach|127\\.0\\.0\\.1:8765")) or (.initialClass | test("chrome-127\\.0\\.0\\.1__-Default"))) | .pid' 2>/dev/null)
    for pid in $pids; do
        if [ -n "$pid" ] && [ "$pid" != "null" ]; then
            kill "$pid" 2>/dev/null || true
        fi
    done
fi

# 4. Desktop notification
if command -v notify-send >/dev/null 2>&1; then
    notify-send -i process-stop "AI English Coach" "已完全退出，后台服务已停止运行。" 2>/dev/null || true
fi
