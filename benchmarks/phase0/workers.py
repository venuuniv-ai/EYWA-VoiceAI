"""Python-side Phase 0 benchmark workers (Piper TTS, Silero VAD, combined client).

Each mode runs in its own process (launched by run_phase0.py under
/usr/bin/time -l) so its peak memory is measured in isolation.
Prints one JSON object on the last stdout line.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PIPER_MODEL = ROOT / "models/tts/en_US-libritts_r-medium.onnx"
SILERO_MODEL = ROOT / "models/vad/silero_vad.onnx"

TTS_SENTENCES = [
    "Hello, thanks for calling.",
    "I can help you book an appointment.",
    "Booking Tuesday at three pm with Doctor Lee, is that right?",
    "Your appointment is confirmed. Is there anything else I can help you with today?",
    "Sorry, I didn't catch that. Could you say the date again?",
]
AGENT_SPEAKER = 0


def read_wav_16k(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, path
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


# ---------------------------------------------------------------- Silero VAD
class SileroVAD:
    """Minimal Silero v5/v6 ONNX wrapper: 512-sample frames at 16 kHz, 64-sample context."""

    FRAME = 512
    CTX = 64

    def __init__(self, path: Path = SILERO_MODEL):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, self.CTX), dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.context, frame.reshape(1, -1).astype(np.float32)], axis=1)
        out, self.state = self.sess.run(None, {"input": x, "state": self.state, "sr": np.array(16000, dtype=np.int64)})
        self.context = x[:, -self.CTX:]
        return float(out[0, 0])


def vad_probs(vad: SileroVAD, audio: np.ndarray) -> tuple[list[float], list[float]]:
    probs, times = [], []
    for i in range(0, len(audio) - SileroVAD.FRAME + 1, SileroVAD.FRAME):
        t0 = time.perf_counter()
        probs.append(vad(audio[i:i + SileroVAD.FRAME]))
        times.append((time.perf_counter() - t0) * 1000)
    return probs, times


def mode_vad(clips: list[str]) -> dict:
    t0 = time.perf_counter()
    vad = SileroVAD()
    load_ms = (time.perf_counter() - t0) * 1000
    per_clip, all_times = [], []
    for c in clips:
        audio = read_wav_16k(Path(c))
        vad.reset()
        probs, times = vad_probs(vad, audio)
        all_times += times
        speech = [p > 0.5 for p in probs]
        per_clip.append({
            "clip": Path(c).name,
            "audio_s": len(audio) / 16000,
            "frames": len(probs),
            "speech_frame_fraction": sum(speech) / max(1, len(speech)),
            "first_speech_s": (speech.index(True) * SileroVAD.FRAME / 16000) if any(speech) else None,
        })
    # Sanity: pure silence should not be detected as speech.
    vad.reset()
    sil_probs, _ = vad_probs(vad, np.zeros(16000 * 2, dtype=np.float32))
    return {"load_ms": load_ms, "frame_ms": SileroVAD.FRAME / 16, "frame_infer_ms": all_times,
            "clips": per_clip, "silence_max_prob": max(sil_probs)}


# ---------------------------------------------------------------- Piper TTS
def load_piper():
    from piper import PiperVoice

    return PiperVoice.load(PIPER_MODEL)


def synth_timed(voice, text: str, speaker: int) -> dict:
    from piper import SynthesisConfig

    cfg = SynthesisConfig(speaker_id=speaker)
    t0 = time.perf_counter()
    first = None
    samples = 0
    for chunk in voice.synthesize(text, cfg):
        if first is None:
            first = (time.perf_counter() - t0) * 1000
        samples += len(chunk.audio_int16_array)
    total = (time.perf_counter() - t0) * 1000
    audio_s = samples / voice.config.sample_rate
    return {"chars": len(text), "first_chunk_ms": first, "total_ms": total, "audio_s": audio_s,
            "rtf": (total / 1000) / audio_s if audio_s else None}


def mode_tts(reps: int) -> dict:
    t0 = time.perf_counter()
    voice = load_piper()
    load_ms = (time.perf_counter() - t0) * 1000
    warm = synth_timed(voice, "Warm up.", AGENT_SPEAKER)
    runs = [synth_timed(voice, s, AGENT_SPEAKER) for _ in range(reps) for s in TTS_SENTENCES]
    return {"load_ms": load_ms, "sample_rate": voice.config.sample_rate, "warmup": warm, "runs": runs}


def mode_make_clips(out_dir: str, speaker: int) -> dict:
    """Synthesize known-text caller clips (different speaker than the agent), 16 kHz mono."""
    import subprocess

    from piper import SynthesisConfig

    scripts = {
        "caller_short": "I'd like to book an appointment.",
        "caller_medium": "Hi, I need to see Doctor Lee next Tuesday afternoon, around three if possible.",
        "caller_long": ("Hello, my name is Sam Rivera. I'm calling to reschedule my appointment from Monday "
                        "to Thursday morning, and I'd also like to ask whether the clinic is open on Saturday."),
    }
    voice = load_piper()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in scripts.items():
        raw = out / f"{name}.22k.wav"
        with wave.open(str(raw), "wb") as w:
            voice.synthesize_wav(text, w, syn_config=SynthesisConfig(speaker_id=speaker))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-ar", "16000", "-ac", "1",
                        "-sample_fmt", "s16", str(out / f"{name}.wav")], check=True)
        raw.unlink()
        (out / f"{name}.txt").write_text(text + "\n")
    return {"clips": sorted(scripts), "speaker": speaker}


# ---------------------------------------------------------------- combined
def mode_combined(clips: list[str], llm_port: int, asr_port: int, rounds: int) -> dict:
    """Load VAD + TTS in this process; use the resident ASR and LLM servers. Exercise all four."""
    from run_phase0 import asr_request, llm_stream  # same directory

    vad = SileroVAD()
    voice = load_piper()
    log = []
    for r in range(rounds):
        for c in clips:
            audio = read_wav_16k(Path(c))
            vad.reset()
            t = time.perf_counter()
            vad_probs(vad, audio)
            vad_ms = (time.perf_counter() - t) * 1000
            asr = asr_request(asr_port, Path(c))
            llm = llm_stream(llm_port, asr["text"], max_tokens=48)
            tts = synth_timed(voice, llm["text"] or "Okay.", AGENT_SPEAKER)
            log.append({"round": r, "clip": Path(c).name, "vad_ms": vad_ms, "asr_ms": asr["latency_ms"],
                        "llm_ttft_ms": llm["ttft_ms"], "tts_first_chunk_ms": tts["first_chunk_ms"]})
    return {"rounds": rounds, "log": log}


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "vad":
        res = mode_vad(sys.argv[2:])
    elif mode == "tts":
        res = mode_tts(int(sys.argv[2]))
    elif mode == "make_clips":
        res = mode_make_clips(sys.argv[2], int(sys.argv[3]))
    elif mode == "combined":
        sys.path.insert(0, str(Path(__file__).parent))
        res = mode_combined(sys.argv[5:], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
    else:
        raise SystemExit(f"unknown mode {mode}")
    print(json.dumps(res))
