from __future__ import annotations

import logging
import signal
import threading
import time

import redis

from meeting_assistant import create_app
from meeting_assistant.services.browser_recorder_job_service import BrowserRecorderJobService
from meeting_assistant.services.recorder_job_queue import RedisRecorderJobQueue


_stop_event = threading.Event()


def _handle_signal(signum, frame) -> None:  # pragma: no cover - operating-system boundary
    _stop_event.set()


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    app = create_app("production")

    with app.app_context():
        queue = app.extensions.get("recorder_job_queue")
        if not isinstance(queue, RedisRecorderJobQueue):
            raise RuntimeError("The production recorder worker requires Redis queue storage.")
        service = BrowserRecorderJobService()
        recovered = _recover_persisted_jobs(queue, service)
        app.logger.info("Recorder worker started; recovered_jobs=%s", recovered)
        next_store_recovery = time.monotonic() + 60

        while not _stop_event.is_set():
            try:
                claim = queue.claim(timeout_seconds=5)
            except redis.RedisError:
                app.logger.exception(
                    "Temporary Redis error while waiting for recorder jobs"
                )
                _stop_event.wait(2)
                continue

            if claim is None:
                if time.monotonic() >= next_store_recovery:
                    _recover_persisted_jobs(queue, service)
                    next_store_recovery = time.monotonic() + 60
                continue

            heartbeat_stop = threading.Event()
            heartbeat = threading.Thread(
                target=_heartbeat_loop,
                args=(queue, claim, heartbeat_stop),
                name=f"recorder-heartbeat-{claim.job_id[:12]}",
            )
            heartbeat.start()
            try:
                service.process_job(claim.job_id)
            except Exception:
                app.logger.exception("Recorder worker could not process job %s", claim.job_id)
                queue.release_for_retry(claim)
            else:
                queue.acknowledge(claim)
            finally:
                heartbeat_stop.set()
                heartbeat.join(timeout=5)

        app.logger.info("Recorder worker stopped.")
    return 0


def _recover_persisted_jobs(
    queue: RedisRecorderJobQueue,
    service: BrowserRecorderJobService,
) -> int:
    recovered = 0
    for job_id in service.recoverable_job_ids():
        if queue.enqueue(job_id):
            recovered += 1
    if recovered:
        logging.getLogger(__name__).info(
            "Returned %s persisted recorder jobs to the Redis queue", recovered
        )
    return recovered


def _heartbeat_loop(queue, claim, stop_event: threading.Event) -> None:
    while not stop_event.wait(30):
        if not queue.heartbeat(claim):
            logging.getLogger(__name__).warning(
                "Recorder job lease was lost for %s", claim.job_id
            )
            return


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
