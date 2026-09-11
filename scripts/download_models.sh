#!/usr/bin/env bash
set -e

# ==============================================================================
# AI English Coach - Model Download Script
# Downloads lightweight local ONNX models:
# 1. Silero VAD (~630 KB)
# 2. SenseVoice-Small ONNX Int8 (~230 MB)
# 3. Kokoro-82M v1.1 Bilingual ONNX + Voices (~360 MB)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODELS_DIR="$ROOT_DIR/models"

mkdir -p "$MODELS_DIR/silero_vad"
mkdir -p "$MODELS_DIR/sensevoice"
mkdir -p "$MODELS_DIR/kokoro"

download_file() {
    local url="$1"
    local dest="$2"
    if [ -s "$dest" ]; then
        echo "✓ Already exists: $dest"
        return 0
    fi
    echo "⬇ Downloading $(basename "$dest")..."
    if command -v curl >/dev/null 2>&1; then
        curl -fSL --progress-bar "$url" -o "$dest"
    elif command -v wget >/dev/null 2>&1; then
        wget --show-progress -qO "$dest" "$url"
    else
        echo "Error: Neither curl nor wget found in PATH."
        exit 1
    fi
}

echo "=========================================================="
echo " Starting AI English Coach model downloads..."
echo " Target directory: $MODELS_DIR"
echo "=========================================================="

# 1. Silero VAD
SILERO_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
download_file "$SILERO_URL" "$MODELS_DIR/silero_vad/silero_vad.onnx"

# 2. SenseVoice-Small (sherpa-onnx release)
SENSEVOICE_ARCHIVE="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"
SENSEVOICE_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$SENSEVOICE_ARCHIVE"
if [ ! -s "$MODELS_DIR/sensevoice/model.int8.onnx" ] || [ ! -s "$MODELS_DIR/sensevoice/tokens.txt" ]; then
    TMP_DIR=$(mktemp -d)
    echo "⬇ Downloading SenseVoice model package..."
    download_file "$SENSEVOICE_URL" "$TMP_DIR/$SENSEVOICE_ARCHIVE"
    echo "📦 Extracting SenseVoice package..."
    tar -xjf "$TMP_DIR/$SENSEVOICE_ARCHIVE" -C "$TMP_DIR"
    cp -f "$TMP_DIR"/sherpa-onnx-sense-voice-*/model.int8.onnx "$MODELS_DIR/sensevoice/"
    cp -f "$TMP_DIR"/sherpa-onnx-sense-voice-*/tokens.txt "$MODELS_DIR/sensevoice/"
    rm -rf "$TMP_DIR"
    echo "✓ SenseVoice model installed."
else
    echo "✓ SenseVoice already installed."
fi

# 3. Kokoro-82M v1.1 zh
KOKORO_ONNX_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.1-zh.onnx"
KOKORO_VOICES_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.1-zh.bin"
download_file "$KOKORO_ONNX_URL" "$MODELS_DIR/kokoro/kokoro-v1.1-zh.onnx"
download_file "$KOKORO_VOICES_URL" "$MODELS_DIR/kokoro/voices-v1.1-zh.bin"

echo ""
echo "=========================================================="
echo " 🎉 All required models are successfully downloaded and ready!"
echo "=========================================================="
