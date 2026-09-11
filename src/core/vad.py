import numpy as np
import sherpa_onnx
from src.config import SILERO_VAD_MODEL

class VadProcessor:
    """
    Voice Activity Detector based on Silero VAD via sherpa-onnx.
    Provides speech boundary detection and silence windowing.
    """
    def __init__(self, sample_rate: int = 16000, min_silence_secs: float = 0.45, min_speech_secs: float = 0.25):
        self.sample_rate = sample_rate
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(SILERO_VAD_MODEL)
        config.silero_vad.min_silence_duration = min_silence_secs
        config.silero_vad.min_speech_duration = min_speech_secs
        config.silero_vad.threshold = 0.5
        config.sample_rate = sample_rate
        config.num_threads = 2
        
        self.detector = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=60)
        self.speech_buffers = []
        self.is_speech_active = False

    def reset(self):
        self.detector.reset()
        self.speech_buffers.clear()
        self.is_speech_active = False

    def accept_waveform(self, samples: np.ndarray) -> bool:
        """
        Accepts float32 audio samples [-1.0, 1.0].
        Returns True if a complete speech segment (utterance) is finished (silence detected after speech).
        """
        if samples.dtype != np.float32:
            samples = samples.astype(np.float32)
            
        self.detector.accept_waveform(samples)
        
        # Check if speech segment is available
        if not self.detector.empty():
            segment = self.detector.front
            self.detector.pop()
            self.speech_buffers.append(np.array(segment.samples, dtype=np.float32))
            return True
            
        return False

    def flush(self) -> bool:
        """Forces detector to flush any speech currently in the buffer."""
        self.detector.flush()
        found = False
        while not self.detector.empty():
            segment = self.detector.front
            self.detector.pop()
            self.speech_buffers.append(np.array(segment.samples, dtype=np.float32))
            found = True
        return found

    def get_speech_audio(self) -> np.ndarray:
        """Returns the accumulated audio samples for the finished utterance."""
        if not self.speech_buffers:
            return np.array([], dtype=np.float32)
        audio = np.concatenate(self.speech_buffers)
        self.speech_buffers.clear()
        return audio
