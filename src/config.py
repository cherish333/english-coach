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

# Bonsai Configuration (Local Ternary-Bonsai-2-27B on port 18022 or custom endpoint)
BONSAI_PORT = int(os.getenv("BONSAI_PORT", "18022"))
BONSAI_BASE_URL = os.getenv("BONSAI_BASE_URL", f"http://127.0.0.1:{BONSAI_PORT}/v1")
BONSAI_API_KEY = os.getenv("BONSAI_API_KEY", "EMPTY")
BONSAI_MODEL_NAME = os.getenv("BONSAI_MODEL_NAME", "prism-ml/Ternary-Bonsai-2-27B-gguf")
BONSAI_MODEL_PATH = os.getenv("BONSAI_MODEL_PATH", "")
BONSAI_LLAMA_SERVER = os.getenv("BONSAI_LLAMA_SERVER", "")
BONSAI_MMPROJ_PATH = os.getenv("BONSAI_MMPROJ_PATH", "")
BONSAI_LORA_PATH = os.getenv("BONSAI_LORA_PATH", "")
BONSAI_LORA_SCALE = float(os.getenv("BONSAI_LORA_SCALE", "2.0"))
BONSAI_CTX_SIZE = int(os.getenv("BONSAI_CTX_SIZE", "16384"))

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
MEDIA_DIR = PROJECT_ROOT / "data" / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# YouTube Importer configuration
YOUTUBE_COOKIES_FILE = Path(os.getenv("YOUTUBE_COOKIES_FILE", str(PROJECT_ROOT / "data" / "youtube_cookies.txt")))
YOUTUBE_BROWSER = os.getenv("YOUTUBE_BROWSER", "").strip().lower()


# Audio & Coach defaults
DEFAULT_VOICE = os.getenv("DEFAULT_VOICE", "zh-CN-XiaoxiaoNeural")  # Microsoft Natural Bilingual Female
DEFAULT_SPEED = float(os.getenv("DEFAULT_SPEED", "1.0"))
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "12"))

# Server settings
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8765"))

# Translation & Word Glosses Engine Configuration
# Options: "auto" (default: DeepL for sentences, NVIDIA/local for glosses), "deepl", "nvidia", "local"
TRANSLATION_ENGINE = os.getenv("TRANSLATION_ENGINE", "auto").strip().lower()

# DeepL API
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY", "").strip()
DEEPL_API_URL = os.getenv(
    "DEEPL_API_URL",
    "https://api-free.deepl.com/v2/translate" if DEEPL_API_KEY.endswith(":fx") else "https://api.deepl.com/v2/translate"
).strip()

# NVIDIA NIM / Cloud OpenAI-Compatible API
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip()
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct").strip()
