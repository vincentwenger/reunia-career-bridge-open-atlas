from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ClaimedRecorderJob:
    job_id: str
    lock_token: str


class RedisRecorderJobQueue:
    """Reliable Redis queue with a processing list and expiring worker lease."""

    def __init__(
        self,
        redis_client,
        *,
        prefix: str = "reunia:recorder-jobs",
        lease_seconds: int = 7200,
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix.rstrip(":")
        self._pending = f"{self._prefix}:pending"
        self._processing = f"{self._prefix}:processing"
        self._lease_seconds = max(60, int(lease_seconds))

    def enqueue(self, job_id: str) -> bool:
        # Keep the deduplication marker and list insertion atomic. A process exit
        # between separate SET and LPUSH commands would otherwise strand the job.
        script = """
        if redis.call('set', KEYS[1], '1', 'NX', 'EX', ARGV[2]) then
            redis.call('lpush', KEYS[2], ARGV[1])
            return 1
        end
        return 0
        """
        return bool(
            self._redis.eval(
                script,
                2,
                self._dedupe_key(job_id),
                self._pending,
                str(job_id),
                7 * 24 * 60 * 60,
            )
        )

    def claim(self, *, timeout_seconds: int = 5) -> ClaimedRecorderJob | None:
        self.recover_abandoned()
        job_id = self._redis.brpoplpush(
            self._pending,
            self._processing,
            timeout=max(1, int(timeout_seconds)),
        )
        if not job_id:
            return None
        token = f"{time.time_ns()}-{threading.get_ident()}"
        if not self._redis.set(
            self._lock_key(job_id),
            token,
            nx=True,
            ex=self._lease_seconds,
        ):
            return None
        return ClaimedRecorderJob(str(job_id), token)

    def heartbeat(self, claim: ClaimedRecorderJob) -> bool:
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('expire', KEYS[1], ARGV[2])
        end
        return 0
        """
        return bool(
            self._redis.eval(
                script,
                1,
                self._lock_key(claim.job_id),
                claim.lock_token,
                self._lease_seconds,
            )
        )

    def acknowledge(self, claim: ClaimedRecorderJob) -> None:
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.lrem(self._processing, 0, claim.job_id)
        pipeline.delete(self._lock_key(claim.job_id))
        pipeline.delete(self._dedupe_key(claim.job_id))
        pipeline.execute()

    def release_for_retry(self, claim: ClaimedRecorderJob) -> None:
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.lrem(self._processing, 0, claim.job_id)
        pipeline.delete(self._lock_key(claim.job_id))
        pipeline.lpush(self._pending, claim.job_id)
        pipeline.execute()

    def recover_abandoned(self) -> int:
        recovered = 0
        for raw_job_id in self._redis.lrange(self._processing, 0, -1):
            job_id = str(raw_job_id)
            if self._redis.exists(self._lock_key(job_id)):
                continue
            pipeline = self._redis.pipeline(transaction=True)
            pipeline.lrem(self._processing, 1, job_id)
            pipeline.lpush(self._pending, job_id)
            removed, _ = pipeline.execute()
            if int(removed or 0) > 0:
                recovered += 1
        return recovered

    def _lock_key(self, job_id: str) -> str:
        return f"{self._prefix}:lock:{job_id}"

    def _dedupe_key(self, job_id: str) -> str:
        return f"{self._prefix}:dedupe:{job_id}"
