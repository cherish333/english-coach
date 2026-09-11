import os
from pathlib import Path
from dotenv import load_dotenv

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"

# Load .env if present
load_dotenv(PROJECT_ROOT / ".env")

# LLM Configuration (Defaulting to local Qwen 3.8-27B vLLM instance on port 18020)
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "http://127.0.0.1:18020/v1")
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "EMPTY")
QWEN_MODEL_NAME = os.getenv("QWEN_MODEL_NAME", "qwen3.8-27b")

# Inference settings (strictly optimized for low-latency voice coach)
LLM_MAX_NEW_TOKENS = int(os.getenv("LLM_MAX_NEW_TOKENS", "1024"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
ENABLE_THINKING = os.getenv("ENABLE_THINKING", "false").lower() in ("true", "1", "yes")

# Model paths
SILERO_VAD_MODEL = MODELS_DIR / "silero_vad" / "silero_vad.onnx"
SENSEVOICE_DIR = MODELS_DIR / "sensevoice"
# The zh export supports Mandarin and English.
KOKORO_MODEL = Path(os.getenv("KOKORO_MODEL", str(MODELS_DIR / "kokoro" / "kokoro-v1.1-zh.onnx")))
KOKORO_VOICES = Path(os.getenv("KOKORO_VOICES", str(MODELS_DIR / "kokoro" / "voices-v1.1-zh.bin")))

# Local textbook sources used by lecture mode. Override these in .env if needed.
BOOKS_DIR = PROJECT_ROOT / "data" / "books"
VOCABULARY_PDF = Path(os.getenv("VOCABULARY_PDF", str(BOOKS_DIR / "vocabulary_in_use.pdf")))
GRAMMAR_PDF = Path(os.getenv("GRAMMAR_PDF", str(BOOKS_DIR / "grammar_in_use.pdf")))
WESTERN_CIV_PDF = Path(os.getenv("WESTERN_CIV_PDF", str(BOOKS_DIR / "western_civilization.pdf")))
CUSTOM_BOOKS_DIR = PROJECT_ROOT / "data" / "custom_books"


# Audio & Coach defaults
DEFAULT_VOICE = os.getenv("DEFAULT_VOICE", "zh-CN-XiaoxiaoNeural")  # Microsoft Natural Bilingual Female
DEFAULT_SPEED = float(os.getenv("DEFAULT_SPEED", "1.0"))
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "12"))

# Server settings
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8765"))
