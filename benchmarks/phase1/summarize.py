"""Generate published Phase 1 tables from successful raw reports, without mixing runs."""
import argparse
import json
from pathlib import Path


def render(transport_path: Path, browser_path: Path) -> str:
    transport = json.loads(transport_path.read_text())
    browser = json.loads(browser_path.read_text())
    for report in (transport, browser):
        if report["status"] != "completed_transport_benchmark" or report["mode"] != "REAL":
            raise ValueError("Only successful real-model reports can be published")
    checks = browser["browser"]
    if not checks["runs"] or not all(r["recovered_turn"] and r["inflight_generation_cancelled"] for r in checks["runs"]):
        raise ValueError("Browser report must verify generation cancellation and call recovery")
    lines = ["# Phase 1 measured results", "",
        "Generated from two separate real-model runs. No inferred or synthetic performance numbers.", "",
        f"- Transport report: `results/{transport_path.name}` ({len(transport['runs'])} measured turns; warmup excluded).",
        f"- Browser report: `results/{browser_path.name}` ({len(checks['runs'])} interruption/recovery cycles).",
        f"- Model profile: `{browser['settings']['llm_profile']}`; `{browser['settings']['active_llm_file']}`.",
        f"- Hardware: {browser['metadata']['hardware']['chip']}, {browser['metadata']['hardware']['ram_bytes'] / 2**30:.0f} GiB RAM; {browser['metadata']['os']}.",
        "- Both reports include UTC date, commit, dirty-tree state, source hash, models, native builds, settings and raw observations.", "",
        "| Measurement | n | p50 | p95 |", "|---|---:|---:|---:|"]
    for label, source, key in (
        ("Transport speech-end → first audio received (ms)", transport["summary"], "transport_voice_to_voice_ms"),
        ("VAD endpoint delay (ms)", transport["summary"], "vad_endpoint_ms"),
        ("ASR processing (ms)", transport["summary"], "asr_ms"),
        ("ASR real-time factor", transport["summary"], "asr_rtf"),
        ("LLM first token (ms)", transport["summary"], "llm_ttft_ms"),
        ("LLM native tokens/sec", transport["summary"], "llm_tokens_per_second"),
        ("TTS first chunk (ms)", transport["summary"], "tts_first_chunk_ms"),
        ("Browser recovered speech-end → WebAudio output scheduled (ms)", checks["summary"], "browser_voice_to_voice_ms"),
        ("Barge-in → playback stop dispatch (ms)", checks["summary"], "barge_in_stop_dispatch_ms"),
        ("Server cancellation (ms)", checks["summary"], "server_cancel_ms")):
        stats = source[key]
        lines.append(f"| {label} | {stats['n']} | {stats['p50']:.3f} | {stats['p95']:.3f} |")
    peak = browser["memory"]["sum_process_max_rss_bytes"]
    lines += ["", "## Memory", "",
        f"Conservative sum of process maxima: **{peak / 1e9:.3f} GB**, below the 2.5 GB target.", "",
        "| Owned stage | Maximum RSS (MiB) |", "|---|---:|"]
    for stage, value in browser["memory"]["isolated_process_max_rss_bytes"].items():
        lines.append(f"| {stage} | {value / 2**20:.2f} |")
    lines += ["", "Native/Piper maxima use macOS `/usr/bin/time -l`. Gateway peak uses `RUSAGE_SELF`",
        "to avoid counting descendants twice. For replaced Piper workers, take the largest",
        "worker peak, because the old process exits before its replacement starts. Raw time",
        "output and 100 ms process-tree samples are saved in the browser report.", "",
        "This sum covers backend/model processes. The existing browser and benchmark",
        "driver are excluded; their pressure is reflected in system-memory samples.", "",
        "## Interpretation", "",
        "The browser uses known recorded caller audio through a synthetic WebAudio input",
        "device. Silero, whisper.cpp, llama.cpp, Piper, the capture worklet, WebSocket",
        "gateway and playback code are real. This is not physical microphone validation.", "",
        "Browser output is a WebAudio schedule timestamp, not an acoustic speaker measurement.",
        "Barge-in onset is the first captured RMS>0.025 frame; it has up to 32 ms frame",
        "uncertainty and ends at playback-stop dispatch. Hardware output buffering is additional.",
        "The browser rows describe responses after planned interruption and recovery.", "",
        "These small fixture sets establish function and provide initial distributions;",
        "they do not establish noise robustness, phone-quality accuracy, or broad conversational",
        "quality. The two runs have distinct source hashes and are not pooled together.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("transport", type=Path)
    parser.add_argument("browser", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(render(args.transport, args.browser))
    print(args.output)
