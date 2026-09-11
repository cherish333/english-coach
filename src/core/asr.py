import re
from pathlib import Path
import numpy as np
import sherpa_onnx
from src.config import SENSEVOICE_DIR

class AsrProcessor:
    """
    SenseVoice-Small Speech Recognition using sherpa-onnx.
    Provides fast, multilingual (zh, en, yue, ja, ko) transcription with rich emotion and event tags.
    """
    def __init__(self, model_dir: Path = None):
        model_dir = model_dir or SENSEVOICE_DIR
        
        # Use quantized int8 model if available, else fp32
        model_path = model_dir / "model.int8.onnx"
        if not model_path.exists():
            model_path = model_dir / "model.onnx"
            
        tokens_path = model_dir / "tokens.txt"

        if not model_path.exists() or not tokens_path.exists():
            raise FileNotFoundError(f"SenseVoice model files not found in {model_dir}")

        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_path),
            tokens=str(tokens_path),
            language="auto",
            use_itn=True,
            num_threads=2,
            debug=False,
            provider="cpu"
        )
        self.sample_rate = 16000

    def transcribe(self, audio_samples: np.ndarray, sample_rate: int = 16000) -> str:
        """
        Transcribes float32 1D audio array.
        Cleans up special emotion/language tags like <|zh|>, <|NEUTRAL|>, etc.
        """
        if len(audio_samples) == 0:
            return ""

        if audio_samples.dtype != np.float32:
            audio_samples = audio_samples.astype(np.float32)

        # Resample or pass stream
        stream = self.recognizer.create_stream()
        stream.accept_waveform(sample_rate, audio_samples)
        self.recognizer.decode_stream(stream)
        
        raw_text = stream.result.text.strip()
        
        # Clean up SenseVoice internal tags: <|zh|>, <|en|>, <|NEUTRAL|>, <|HAPPY|>, <|withitn|>, etc.
        clean_text = re.sub(r'<\|[^|>]+(?:\|>)?', '', raw_text).strip()
        return clean_text
