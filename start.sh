#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Ensure venv exists
if [ ! -d "$DIR/.venv" ]; then
    echo "Virtual environment not found! Setting up..."
    ~/.local/bin/uv venv --python 3.12 .venv
    ~/.local/bin/uv pip install --python "$DIR/.venv/bin/python" \
        fastapi "uvicorn[standard]" websockets openai soundfile numpy sherpa-onnx kokoro-onnx python-dotenv httpx "misaki[zh]" PyMuPDF edge-tts
fi

if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "Error: .venv exists but .venv/bin/python is missing or not executable."
    exit 1
fi

# Activate venv
source "$DIR/.venv/bin/activate"

# Check all model files before opening the HTTP port.  Missing token/voice
# files otherwise only fail on the first request and look like a server bug.
required_models=(
    "$DIR/models/silero_vad/silero_vad.onnx"
    "$DIR/models/sensevoice/model.int8.onnx"
    "$DIR/models/sensevoice/tokens.txt"
    "$DIR/models/kokoro/kokoro-v1.1-zh.onnx"
    "$DIR/models/kokoro/voices-v1.1-zh.bin"
)
for model_file in "${required_models[@]}"; do
    if [ ! -s "$model_file" ]; then
        echo "Error: Required model file missing or empty: $model_file"
        echo "Tip: Run 'bash scripts/download_models.sh' to download missing models automatically."
        exit 1
    fi
done

# Fail early on an incomplete/stale virtualenv instead of accepting a broken
# process and discovering the problem through a WebSocket request.
if ! python -c 'import fastapi, httpx, kokoro_onnx, misaki, numpy, openai, pymupdf, sherpa_onnx, soundfile, edge_tts'; then
    echo "Error: Python dependencies are incomplete in $DIR/.venv"
    echo "Recreate the environment or install the packages listed in start.sh."
    exit 1
fi

echo "=========================================================="
echo " 🎙️  AI English Coach is starting..."
echo " - VAD: Silero VAD (CPU)"
echo " - ASR: SenseVoice-Small ONNX (CPU)"
echo " - TTS: 微软高拟真中英双语 Neural TTS (默认) + Kokoro-82M (离线备用)"
echo " - LLM: Qwen3.8-27B (RTX 3090 at http://127.0.0.1:18020/v1)"
echo " - 横屏主控台: http://localhost:8765"
echo " - 竖屏教材端: http://localhost:8765/portrait"
echo "=========================================================="

exec python -m src.server
