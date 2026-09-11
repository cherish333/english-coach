import io
import re
import asyncio
import logging
from collections import OrderedDict
import soundfile as sf
import numpy as np
from typing import Tuple, List, Dict, Any
from kokoro_onnx import Kokoro
from misaki.zh import ZHG2P
import edge_tts
from src.config import KOKORO_MODEL, KOKORO_VOICES, DEFAULT_VOICE, DEFAULT_SPEED

logger = logging.getLogger("english_coach.tts")

def clean_tts_text(text: str) -> str:
    """Pre-processes text to ensure clean, high-quality pronunciation without symbols."""
    # Remove XML/HTML tags
    text = re.sub(r"</?[^>]+>", "", text)
    # Strip markdown bold, italics, code, headings
    text = re.sub(r"[\*`_~#]", "", text)
    # Normalize quotes and dashes
    text = text.replace("“", "\"").replace("”", "\"").replace("‘", "'").replace("’", "'")
    text = re.sub(r"[—–]+", ", ", text)
    # Keep standard characters, spaces, and punctuation
    text = re.sub(r"[^\w\s.,!?:;\"'\-()，。！？：；（）、\u4e00-\u9fff]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

EDGE_VOICES_MAP = {
    "zh-CN-XiaoxiaoNeural": {"name": "晓晓 · 微软中英双语女声 (自然抑扬 · 极力推荐)", "engine": "edge", "recommended": True},
    "zh-CN-YunxiNeural": {"name": "云希 · 微软中英双语男声 (阳光自然 · 推荐)", "engine": "edge", "recommended": True},
    "zh-CN-YunjianNeural": {"name": "云健 · 微软中英双语男声 (沉稳教学风)", "engine": "edge", "recommended": False},
    "zh-CN-XiaoyiNeural": {"name": "晓伊 · 微软中英双语女声 (亲切生动)", "engine": "edge", "recommended": False},
    "en-US-JennyNeural": {"name": "Jenny · 微软纯正美语女声 (原汁原味 · 极力推荐)", "engine": "edge", "recommended": True},
    "en-US-GuyNeural": {"name": "Guy · 微软纯正美语男声", "engine": "edge", "recommended": False},
    "en-GB-SoniaNeural": {"name": "Sonia · 微软纯正英语女声 (英音经典 · 推荐)", "engine": "edge", "recommended": True},
}

KOKORO_VOICES_MAP = {
    "af_maple": {"name": "Maple · 本地美音女声 (Kokoro离线备用)", "engine": "kokoro", "recommended": False},
    "bf_vale": {"name": "Vale · 本地英音女声 (Kokoro离线备用)", "engine": "kokoro", "recommended": False},
    "zf_001": {"name": "001 · 本地中文女声 (Kokoro离线备用)", "engine": "kokoro", "recommended": False},
    "zm_009": {"name": "009 · 本地中文男声 (Kokoro离线备用)", "engine": "kokoro", "recommended": False},
}

class TtsProcessor:
    """
    Hybrid High-Fidelity TTS Engine.
    - Primary: Microsoft Edge-TTS Neural voices (state-of-the-art Chinese-English code-switching)
    - Fallback/Offline: Kokoro-82M ONNX local CPU engine
    """
    def __init__(self, executor=None):
        self.default_voice = DEFAULT_VOICE
        self.default_speed = DEFAULT_SPEED
        self.executor = executor
        self._audio_cache: OrderedDict[Tuple[str, str, float], Tuple[bytes, str]] = OrderedDict()
        # Lazy initialization for local Kokoro to save memory until needed
        self._kokoro_engine = None
        self._zh_g2p = None

    def _ensure_kokoro(self):
        if self._kokoro_engine is None:
            self._kokoro_engine = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
            self._zh_g2p = ZHG2P()

    def get_available_voices(self) -> List[str]:
        return list(EDGE_VOICES_MAP.keys()) + list(KOKORO_VOICES_MAP.keys())

    def get_voice_catalog(self) -> List[Dict[str, Any]]:
        catalog = []
        for vid, meta in EDGE_VOICES_MAP.items():
            catalog.append({"id": vid, **meta})
        for vid, meta in KOKORO_VOICES_MAP.items():
            catalog.append({"id": vid, **meta})
        return catalog

    def generate_kokoro_wav(self, text: str, voice: str = "af_maple", speed: float = 1.0) -> bytes:
        """Synthesizes using local Kokoro engine (returns WAV bytes). Synchronous CPU task."""
        self._ensure_kokoro()
        text = clean_tts_text(text)
        if not text:
            return b""

        has_chinese = bool(re.search(r'[\u4e00-\u9fff]', text))
        if has_chinese:
            phonemes, _ = self._zh_g2p(text)
            if "❓" in phonemes:
                phonemes = phonemes.replace("❓", "")
            phonemes = phonemes.strip()
            if not phonemes:
                return b""

            samples, sr = self._kokoro_engine.create(
                phonemes,
                voice=voice if voice in KOKORO_VOICES_MAP else "af_maple",
                speed=speed,
                lang="cmn",
                is_phonemes=True,
            )
        elif voice.startswith("b"):
            samples, sr = self._kokoro_engine.create(text, voice=voice, speed=speed, lang="en-gb")
        else:
            samples, sr = self._kokoro_engine.create(text, voice=voice, speed=speed, lang="en-us")

        buf = io.BytesIO()
        sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    async def generate_kokoro_wav_async(self, text: str, voice: str = "af_maple", speed: float = 1.0) -> bytes:
        """Asynchronously executes CPU-intensive Kokoro ONNX generation in an executor thread."""
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self.executor,
            self.generate_kokoro_wav,
            text,
            voice,
            speed
        )

    async def generate_audio(self, text: str, voice: str = None, speed: float = None) -> Tuple[bytes, str]:
        """
        Synthesizes text into audio bytes and returns (audio_bytes, mime_type).
        - Edge-TTS produces high-fidelity MP3 ('audio/mpeg').
        - Kokoro fallback produces WAV ('audio/wav').
        Non-blocking: Kokoro runs inside a background worker thread.
        """
        text = clean_tts_text(text)
        if not text:
            return b"", "audio/mpeg"

        voice = voice or self.default_voice
        speed = speed or self.default_speed

        cache_key = (text, voice, round(float(speed), 2))
        if cache_key in self._audio_cache:
            cached_bytes, cached_mime = self._audio_cache[cache_key]
            self._audio_cache.move_to_end(cache_key)
            return cached_bytes, cached_mime

        # If explicitly requesting Kokoro voice
        if voice in KOKORO_VOICES_MAP:
            wav_bytes = await self.generate_kokoro_wav_async(text, voice=voice, speed=speed)
            if wav_bytes and len(text) <= 60 and len(text.split()) <= 10:
                self._audio_cache[cache_key] = (wav_bytes, "audio/wav")
                self._audio_cache.move_to_end(cache_key)
                while len(self._audio_cache) > 3000:
                    self._audio_cache.popitem(last=False)
            return wav_bytes, "audio/wav"

        # Edge-TTS Neural Voice synthesis (Highest quality bilingual code-switching)
        rate_percent = int((speed - 1.0) * 100)
        rate_str = f"{rate_percent:+d}%"

        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate_str)
            audio_chunks = []

            async def _collect_stream():
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_chunks.append(chunk["data"])

            await asyncio.wait_for(_collect_stream(), timeout=10.0)
            audio_bytes = b"".join(audio_chunks)
            if audio_bytes:
                if len(text) <= 60 and len(text.split()) <= 10:
                    self._audio_cache[cache_key] = (audio_bytes, "audio/mpeg")
                    self._audio_cache.move_to_end(cache_key)
                    while len(self._audio_cache) > 3000:
                        self._audio_cache.popitem(last=False)
                return audio_bytes, "audio/mpeg"
        except Exception as exc:
            logger.warning(f"Edge-TTS synthesis error ({exc}), falling back to local Kokoro engine...")

        # Fallback to local Kokoro (non-blocking in worker thread)
        try:
            wav_bytes = await self.generate_kokoro_wav_async(text, voice="af_maple", speed=speed)
            if wav_bytes and len(text) <= 60 and len(text.split()) <= 10:
                self._audio_cache[cache_key] = (wav_bytes, "audio/wav")
                self._audio_cache.move_to_end(cache_key)
                while len(self._audio_cache) > 3000:
                    self._audio_cache.popitem(last=False)
            return wav_bytes, "audio/wav"
        except Exception as e:
            logger.error(f"Kokoro fallback synthesis also failed: {e}")
            return b"", "audio/wav"
