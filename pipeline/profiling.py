"""Optional macOS peak-RSS measurement; normal application startup uses no wrapper."""
import os
import signal
from pathlib import Path

import psutil


def measured_command(command: list[str], name: str) -> tuple[list[str], bool]:
    directory = os.environ.get("EYWA_PROFILE_DIR")
    if not directory:
        return command, False
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    return ["/usr/bin/time", "-l", "-o", str(path / f"{name}.txt"), *command], True


def signal_owned(pid: int, profiled: bool, force: bool = False):
    """Signal the owned command, allowing its time wrapper to write peak usage."""
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        process = psutil.Process(pid)
        targets = process.children() if profiled else [process]
        for target in targets:
            try:
                target.send_signal(sig)
            except psutil.NoSuchProcess:
                pass
        if force and profiled:
            process.kill()
    except psutil.NoSuchProcess:
        pass
