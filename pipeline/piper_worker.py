"""Resident Piper worker. Private stdio IPC only; never logs speech text."""
import base64
import json
import sys

from piper import PiperVoice, SynthesisConfig


def main():
    voice = PiperVoice.load(sys.argv[1])
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            text = request["text"]
            speaker = request.get("speaker", 0)
            if speaker not in (0, 20):
                raise ValueError("Only configured agent/caller voices are allowed")
            for chunk in voice.synthesize(text, SynthesisConfig(speaker_id=speaker)):
                # 80 ms transport chunks; Piper itself produces one sentence at a time.
                audio = chunk.audio_int16_bytes
                size = round(chunk.sample_rate * 0.08) * 2
                for i in range(0, len(audio), size):
                    print(json.dumps({"rate": chunk.sample_rate,
                        "pcm": base64.b64encode(audio[i:i + size]).decode()}), flush=True)
            print(json.dumps({"done": True}), flush=True)
        except Exception:
            print(json.dumps({"error": "Piper synthesis failed"}), flush=True)


if __name__ == "__main__":
    main()
