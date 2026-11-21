"""Local-only OTel traces. No transcripts, audio, prompts, or exception strings."""
from pathlib import Path

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter


class LocalTracing:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        # Bounded local retention; traces contain timing/IDs only.
        import time
        for old in directory.glob("*.jsonl"):
            if old.stat().st_mtime < time.time() - 7 * 86400:
                old.unlink()
        self.file = (directory / f"spans-{time.time_ns()}.jsonl").open("x")
        self.provider = TracerProvider(resource=Resource.create({"service.name": "eywa-local"}))
        self.provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(
            out=self.file, formatter=lambda span: span.to_json(indent=None) + "\n")))
        self.tracer = self.provider.get_tracer("eywa.pipeline")

    def span(self, name: str, turn_id: int):
        return self.tracer.start_as_current_span(name, attributes={"turn.id": turn_id},
            record_exception=False, set_status_on_exception=False)

    def close(self):
        self.provider.shutdown()
        self.file.close()
