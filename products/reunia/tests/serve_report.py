#!/usr/bin/env python3
"""Serve and open the latest Réunia HTML test report.

The server is intentionally temporary. It serves only the tests folder, opens
``test-report.html`` in the default browser, and stops automatically after the
configured timeout.
"""

from __future__ import annotations

import argparse
import http.server
import threading
import webbrowser
from functools import partial
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
REPORT_HTML = "test-report.html"
RESULTS_JSON = "test-results.json"


class NoCacheRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serve files from tests/ while preventing stale report caching."""

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Keep the temporary server quiet for non-technical users.
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Local address used by the temporary report server.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local port. Use 0 to choose an available port automatically.",
    )
    parser.add_argument(
        "--seconds",
        type=int,
        default=900,
        help="Automatically stop the temporary server after this many seconds.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Start the server without opening the default web browser.",
    )
    return parser.parse_args()


def require_report_files() -> None:
    missing = [
        filename
        for filename in (REPORT_HTML, RESULTS_JSON)
        if not (TESTS_DIR / filename).is_file()
    ]
    if missing:
        names = ", ".join(missing)
        raise SystemExit(
            f"Cannot open the test report because these files are missing: {names}"
        )


def main() -> int:
    args = parse_args()
    require_report_files()

    handler = partial(NoCacheRequestHandler, directory=str(TESTS_DIR))
    server = http.server.ThreadingHTTPServer((args.host, args.port), handler)
    server.daemon_threads = True

    host, port = server.server_address[:2]
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    report_url = f"http://{browser_host}:{port}/{REPORT_HTML}"

    if args.seconds > 0:
        shutdown_timer = threading.Timer(args.seconds, server.shutdown)
        shutdown_timer.daemon = True
        shutdown_timer.start()
    else:
        shutdown_timer = None

    if not args.no_browser:
        open_timer = threading.Timer(0.35, webbrowser.open, args=(report_url,))
        open_timer.daemon = True
        open_timer.start()

    print(f"Test report opened at: {report_url}")
    print(
        "The temporary report server will close automatically "
        f"after {args.seconds} seconds."
        if args.seconds > 0
        else "Press Ctrl+C to stop the temporary report server."
    )

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        if shutdown_timer is not None:
            shutdown_timer.cancel()
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
