#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=========================================================="
echo " 🛑 Stopping AI English Coach..."
echo "=========================================================="

# 1. Terminate the local python coach server processes
PIDS=$(pgrep -f "src\.server" || true)
if [ -n "$PIDS" ]; then
    echo "Found running Coach server process: $PIDS"
    kill -15 $PIDS 2>/dev/null || true
    sleep 1
    # Force kill if still alive
    kill -9 $PIDS 2>/dev/null || true
    echo " Coach server stopped."
else
    echo "ℹ️ Coach server is not currently running."
fi

# 2. Check if GPU container is running
QWEN_CONTAINER=$(docker ps -q -f "name=qwen38-27b-rtx3090" 2>/dev/null || true)
if [ -n "$QWEN_CONTAINER" ]; then
    echo ""
    echo "⚠️  Local Qwen3.8-27B Docker container is currently running and occupying RTX 3090 VRAM."
    if [ "$1" == "--gpu" ] || [ "$1" == "--all" ] || [ "$1" == "-a" ]; then
        echo " Stopping Qwen GPU container to release VRAM..."
        docker stop $QWEN_CONTAINER >/dev/null 2>&1 || true
        echo " GPU VRAM released successfully!"
    else
        echo "💡 To stop Qwen and completely release RTX 3090 GPU VRAM, run:"
        echo "   ./stop.sh --gpu"
        echo "   (or: docker stop $QWEN_CONTAINER)"
    fi
fi

echo "=========================================================="
echo " Done."
echo "=========================================================="
