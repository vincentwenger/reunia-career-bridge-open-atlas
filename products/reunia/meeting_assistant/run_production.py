from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


_children: list[subprocess.Popen] = []


def _terminate_children(signum=None, frame=None) -> None:  # pragma: no cover
    for child in _children:
        if child.poll() is None:
            child.terminate()
    deadline = time.time() + 15
    for child in _children:
        remaining = max(0, deadline - time.time())
        try:
            child.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            child.kill()


def main() -> int:
    signal.signal(signal.SIGTERM, _terminate_children)
    signal.signal(signal.SIGINT, _terminate_children)

    worker = subprocess.Popen([sys.executable, "-m", "meeting_assistant.recorder_worker"])
    web = subprocess.Popen(
        [
            "gunicorn",
            "--bind", "0.0.0.0:5000",
            "--workers", os.getenv("GUNICORN_WORKERS", "2"),
            "--threads", os.getenv("GUNICORN_THREADS", "4"),
            "--timeout", os.getenv("GUNICORN_TIMEOUT", "900"),
            "wsgi:app",
        ]
    )
    _children.extend([worker, web])

    while True:
        if worker.poll() is not None:
            _terminate_children()
            return int(worker.returncode or 1)
        if web.poll() is not None:
            _terminate_children()
            return int(web.returncode or 1)
        time.sleep(1)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
