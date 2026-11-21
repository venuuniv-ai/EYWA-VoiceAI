"""Production Silero wrapper; 512-sample PCM16 frames at 16 kHz."""
from pathlib import Path

import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[1]


class SileroVAD:
    def __init__(self, path: Path = ROOT / "models/vad/silero_vad.onnx"):
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def probability(self, pcm: bytes) -> float:
        if len(pcm) != 1024:
            raise ValueError("VAD requires exactly 512 PCM16 samples")
        frame = np.frombuffer(pcm, dtype="<i2").astype(np.float32)[None, :] / 32768
        x = np.concatenate((self.context, frame), axis=1)
        result, self.state = self.session.run(None, {
            "input": x, "state": self.state, "sr": np.array(16000, dtype=np.int64),
        })
        self.context = x[:, -64:]
        return float(result[0, 0])
