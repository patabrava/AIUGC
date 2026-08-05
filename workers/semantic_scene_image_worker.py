"""Durable bounded-concurrency worker for one Semantic UGC image per script."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
import os
import socket
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable, Optional
from uuid import uuid4

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.features.semantic_videos import queries
from app.features.semantic_videos.handlers import generate_candidates


logger = get_logger(__name__)
DEFAULT_CONCURRENCY = 2
DEFAULT_LEASE_SECONDS = 180
DEFAULT_HEARTBEAT_SECONDS = 20.0
DEFAULT_PROCESS_HEARTBEAT_SECONDS = 15.0
CONTROL_RPC_TIMEOUT_SECONDS = 5.0
MIN_EXECUTION_BUDGET_SECONDS = 240.0
PROVIDER_OVERHEAD_BUDGET_SECONDS = 60.0
MAX_PROVIDER_ATTEMPT_SECONDS = 120.0
# Adapter phase budgets, including provider-capacity wait, sum to this limit.
MAX_PROVIDER_CALL_WALLCLOCK_SECONDS = MAX_PROVIDER_ATTEMPT_SECONDS
LEASE_RECLAIM_SAFETY_SECONDS = 30.0


class _SceneImageLeaseGuard:
    """Sticky execution fence shared by lease renewal and provider stages."""

    def __init__(self, *, lease_expires_at: datetime) -> None:
        self._lost = threading.Event()
        self._reason = ""
        self._lock = threading.Lock()
        self._lease_expires_at = lease_expires_at

    @staticmethod
    def _parse_expiry(lease_expires_at: Any) -> Optional[datetime]:
        try:
            parsed = datetime.fromisoformat(
                str(lease_expires_at or "").replace("Z", "+00:00")
            )
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def confirm(self, lease_expires_at: Any) -> None:
        parsed = self._parse_expiry(lease_expires_at)
        if parsed is None:
            return
        with self._lock:
            if parsed > self._lease_expires_at:
                self._lease_expires_at = parsed

    def reconcile(
        self,
        lease_expires_at: Any,
        *,
        minimum_remaining_seconds: float,
    ) -> bool:
        """Accept an ambiguous renewal only when it advanced or remains safe."""
        parsed = self._parse_expiry(lease_expires_at)
        if parsed is None:
            return False
        with self._lock:
            advanced = parsed > self._lease_expires_at
            if advanced:
                self._lease_expires_at = parsed
            confirmed_expiry = self._lease_expires_at
        remaining = (confirmed_expiry - datetime.now(timezone.utc)).total_seconds()
        return advanced or remaining >= float(minimum_remaining_seconds)

    def lose(self, reason: str) -> None:
        self._reason = str(reason or "scene-image lease renewal failed")[:200]
        self._lost.set()

    def assert_active(self) -> None:
        with self._lock:
            expired = datetime.now(timezone.utc) >= self._lease_expires_at
        if expired:
            self.lose("last confirmed scene-image lease expired")
        if self._lost.is_set():
            raise ValidationError(
                "Scene-image worker lost its durable lease.",
                {"reason": self._reason},
            )

    def assert_provider_window(self, required_seconds: float) -> None:
        """Fence a paid provider call behind a full confirmed lease window."""
        self.assert_active()
        with self._lock:
            remaining = (
                self._lease_expires_at - datetime.now(timezone.utc)
            ).total_seconds()
        if remaining < float(required_seconds):
            self.lose("confirmed scene-image lease cannot cover the provider call")
            raise ValidationError(
                "Scene-image worker lease is too short for a provider attempt.",
                {
                    "remaining_seconds": max(0.0, round(remaining, 3)),
                    "required_seconds": float(required_seconds),
                },
            )


class _SceneImageLeaseHeartbeat:
    """Keep the PostgreSQL job lease alive during blocking provider work."""

    def __init__(
        self,
        *,
        repo: Any,
        job_id: str,
        post_id: str,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
        interval_seconds: float,
        guard: _SceneImageLeaseGuard,
    ) -> None:
        self.repo = repo
        self.job_id = job_id
        self.post_id = post_id
        self.worker_id = worker_id
        self.lease_token = lease_token
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self.guard = guard
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"semantic-scene-image-lease-{job_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=min(10.0, max(1.0, self.lease_seconds / 4)))
        if self._thread.is_alive():
            logger.warning(
                "semantic_scene_image_lease_heartbeat_stop_timeout",
                job_id=self.job_id,
                worker_id=self.worker_id,
            )

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                renewed = self.repo.renew_scene_image_job(
                    job_id=self.job_id,
                    worker_id=self.worker_id,
                    lease_token=self.lease_token,
                    lease_seconds=self.lease_seconds,
                    timeout_seconds=CONTROL_RPC_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "semantic_scene_image_lease_renewal_failed",
                    job_id=self.job_id,
                    worker_id=self.worker_id,
                    error=str(exc),
                )
                try:
                    persisted = self.repo.get_scene_image_job(
                        self.post_id,
                        timeout_seconds=CONTROL_RPC_TIMEOUT_SECONDS,
                    )
                except Exception as reconcile_exc:  # noqa: BLE001
                    logger.exception(
                        "semantic_scene_image_lease_renewal_ambiguous",
                        job_id=self.job_id,
                        worker_id=self.worker_id,
                        error=str(reconcile_exc),
                    )
                    continue
                persisted_expiry = str(
                    (persisted or {}).get("lease_expires_at") or ""
                )
                exact_lease_survived = bool(
                    isinstance(persisted, dict)
                    and str(persisted.get("id") or "") == self.job_id
                    and str(persisted.get("status") or "") == "processing"
                    and str(persisted.get("worker_id") or "") == self.worker_id
                    and str(persisted.get("lease_token") or "") == self.lease_token
                    and persisted_expiry
                )
                if exact_lease_survived:
                    exact_lease_survived = self.guard.reconcile(
                        persisted_expiry,
                        minimum_remaining_seconds=(
                            MAX_PROVIDER_CALL_WALLCLOCK_SECONDS
                            + LEASE_RECLAIM_SAFETY_SECONDS
                        ),
                    )
                if exact_lease_survived:
                    logger.warning(
                        "semantic_scene_image_lease_renewal_reconciled",
                        job_id=self.job_id,
                        worker_id=self.worker_id,
                        lease_expires_at=persisted_expiry,
                    )
                    continue
                self.guard.lose("scene-image lease ownership changed")
                continue
            self.guard.confirm((renewed or {}).get("lease_expires_at"))
            logger.info(
                "semantic_scene_image_lease_renewed",
                job_id=self.job_id,
                worker_id=self.worker_id,
                lease_expires_at=(renewed or {}).get("lease_expires_at"),
            )


class SemanticSceneImageWorker:
    def __init__(
        self,
        *,
        repo: Any = queries,
        worker_id: Optional[str] = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    ) -> None:
        if lease_seconds < 180 or lease_seconds > 300:
            raise ValidationError("Scene-image lease must be between 180 and 300 seconds.")
        if heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds:
            raise ValidationError(
                "Scene-image heartbeat must be positive and shorter than the lease."
            )
        if (
            lease_seconds - heartbeat_seconds
            < MAX_PROVIDER_CALL_WALLCLOCK_SECONDS + LEASE_RECLAIM_SAFETY_SECONDS
        ):
            raise ValidationError(
                "Scene-image lease does not cover the maximum provider lifecycle."
            )
        self.repo = repo
        self.worker_id = worker_id or (
            "semantic-scene-image-v2-"
            f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
        )
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = float(heartbeat_seconds)

    def tick(
        self,
        *,
        on_claim_probe: Optional[Callable[[bool, Optional[str]], None]] = None,
    ) -> dict[str, Any]:
        try:
            job = self.repo.claim_scene_image_job(
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                timeout_seconds=CONTROL_RPC_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            if on_claim_probe is not None:
                on_claim_probe(False, type(exc).__name__)
            raise
        if on_claim_probe is not None:
            on_claim_probe(True, None)
        if not job:
            return {"action": "not_claimed"}

        job_id = str(job["id"])
        lease_token = str(job["lease_token"])
        post_id = str(job["post_id"])
        deadline_text = str(job.get("deadline_at") or "").strip()
        try:
            deadline_at = datetime.fromisoformat(deadline_text.replace("Z", "+00:00"))
        except ValueError:
            deadline_at = None
        if deadline_at is not None and deadline_at.tzinfo is None:
            deadline_at = deadline_at.replace(tzinfo=timezone.utc)
        remaining_seconds = (
            (deadline_at - datetime.now(timezone.utc)).total_seconds()
            if deadline_at is not None
            else -1.0
        )
        if remaining_seconds < MIN_EXECUTION_BUDGET_SECONDS:
            error = {
                "code": "insufficient_execution_budget",
                "message": "The image job no longer has enough safe provider time. Retry this script.",
            }
            self.repo.finish_scene_image_job(
                job_id=job_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                status="failed",
                error=error,
                timeout_seconds=CONTROL_RPC_TIMEOUT_SECONDS,
            )
            return {"action": "failed", "job_id": job_id, "error": error}
        provider_timeout_seconds = min(
            MAX_PROVIDER_ATTEMPT_SECONDS,
            max(30.0, self.lease_seconds - self.heartbeat_seconds - 10.0),
            max(
                30.0,
                (remaining_seconds - PROVIDER_OVERHEAD_BUDGET_SECONDS) / 2.0,
            ),
        )
        initial_lease_expiry_text = str(job.get("lease_expires_at") or "").strip()
        try:
            initial_lease_expiry = datetime.fromisoformat(
                initial_lease_expiry_text.replace("Z", "+00:00")
            )
        except ValueError:
            initial_lease_expiry = datetime.now(timezone.utc) + timedelta(
                seconds=self.lease_seconds
            )
        if initial_lease_expiry.tzinfo is None:
            initial_lease_expiry = initial_lease_expiry.replace(tzinfo=timezone.utc)
        lease_guard = _SceneImageLeaseGuard(
            lease_expires_at=initial_lease_expiry
        )
        request = SimpleNamespace(
            state=SimpleNamespace(
                user_email=str(job.get("requested_by") or "scene-image-worker"),
                correlation_id=str(job.get("correlation_id") or job_id),
                scene_image_job_id=job_id,
                scene_image_worker_id=self.worker_id,
                scene_image_lease_token=lease_token,
                scene_image_expected_run_id=str(
                    job.get("run_id") or job.get("expected_run_id") or ""
                ),
                scene_image_provider_timeout_seconds=provider_timeout_seconds,
                scene_image_deadline_at=deadline_text,
                scene_image_lease_guard=lease_guard,
            )
        )
        heartbeat = _SceneImageLeaseHeartbeat(
            repo=self.repo,
            job_id=job_id,
            post_id=post_id,
            worker_id=self.worker_id,
            lease_token=lease_token,
            lease_seconds=self.lease_seconds,
            interval_seconds=self.heartbeat_seconds,
            guard=lease_guard,
        )
        heartbeat.start()
        try:
            result = generate_candidates(
                post_id,
                SimpleNamespace(
                    candidate_count=1,
                    expected_revision=job.get("expected_revision"),
                ),
                request,
            )
            run_id = str((result.data or {}).get("run_id") or "")
            if not run_id:
                raise ValidationError("Scene-image generation returned no persisted run.")
            # The queue completion and run finalization commit in one RPC inside
            # generate_candidates. A second acknowledgement here would reopen
            # the exact crash window this worker is designed to remove.
            logger.info(
                "semantic_scene_image_completed",
                job_id=job_id,
                post_id=post_id,
                run_id=run_id,
            )
            return {"action": "completed", "job_id": job_id, "run_id": run_id}
        except Exception as exc:  # noqa: BLE001
            try:
                persisted = self.repo.get_scene_image_job(
                    post_id,
                    timeout_seconds=CONTROL_RPC_TIMEOUT_SECONDS,
                )
            except Exception:  # noqa: BLE001
                persisted = None
            if (
                isinstance(persisted, dict)
                and str(persisted.get("id") or "") == job_id
                and str(persisted.get("status") or "") == "completed"
                and str(persisted.get("run_id") or "")
            ):
                logger.warning(
                    "semantic_scene_image_completion_reconciled",
                    job_id=job_id,
                    post_id=post_id,
                    error=str(exc),
                )
                return {
                    "action": "completed",
                    "job_id": job_id,
                    "run_id": str(persisted["run_id"]),
                }
            error = {"code": type(exc).__name__, "message": str(exc)[:500]}
            try:
                self.repo.finish_scene_image_job(
                    job_id=job_id,
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                    status="failed",
                    error=error,
                    timeout_seconds=CONTROL_RPC_TIMEOUT_SECONDS,
                )
            except Exception as finish_exc:  # noqa: BLE001
                logger.exception(
                    "semantic_scene_image_failure_persistence_failed",
                    job_id=job_id,
                    error=str(finish_exc),
                )
            logger.exception(
                "semantic_scene_image_failed",
                job_id=job_id,
                post_id=post_id,
                error=str(exc),
            )
            return {"action": "failed", "job_id": job_id, "error": error}
        finally:
            heartbeat.stop()


def _publish_process_heartbeat(
    worker: SemanticSceneImageWorker, *, active_count: int, concurrency: int
) -> dict[str, Any]:
    """Probe queue access independently from claim/generation futures."""
    probe_checked_at = datetime.now(timezone.utc).isoformat()
    try:
        worker.repo.probe_scene_image_queue(
            timeout_seconds=CONTROL_RPC_TIMEOUT_SECONDS
        )
    except Exception as probe_exc:  # noqa: BLE001
        probe_status = "error"
        probe_error_class = type(probe_exc).__name__
        logger.exception(
            "semantic_scene_image_queue_probe_failed",
            worker_id=worker.worker_id,
            error=str(probe_exc),
        )
    else:
        probe_status = "ok"
        probe_error_class = None
    metadata = {
        "contract": "semantic-scene-image-v2",
        "concurrency": concurrency,
        "active": active_count,
        "queue_probe_status": probe_status,
        "queue_probe_checked_at": probe_checked_at,
        "queue_probe_error_class": probe_error_class,
    }
    worker.repo.heartbeat_scene_image_worker(
        worker_id=worker.worker_id,
        metadata=metadata,
        timeout_seconds=CONTROL_RPC_TIMEOUT_SECONDS,
    )
    return metadata


def main() -> None:
    concurrency = int(
        os.getenv("SEMANTIC_SCENE_IMAGE_WORKER_CONCURRENCY", str(DEFAULT_CONCURRENCY))
    )
    if concurrency < 1 or concurrency > 2:
        raise ValidationError("Scene-image worker concurrency must be one or two.")
    poll_seconds = max(
        0.25, float(os.getenv("SEMANTIC_SCENE_IMAGE_WORKER_POLL_SECONDS", "1"))
    )
    worker = SemanticSceneImageWorker()
    logger.info(
        "semantic_scene_image_worker_started",
        worker_id=worker.worker_id,
        concurrency=concurrency,
    )
    active: set[Future[dict[str, Any]]] = set()
    last_process_heartbeat = 0.0
    with ThreadPoolExecutor(
        max_workers=concurrency,
        thread_name_prefix="semantic-scene-image",
    ) as executor:
        while True:
            now = time.monotonic()
            if now - last_process_heartbeat >= DEFAULT_PROCESS_HEARTBEAT_SECONDS:
                try:
                    _publish_process_heartbeat(
                        worker,
                        active_count=len(active),
                        concurrency=concurrency,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "semantic_scene_image_worker_heartbeat_failed",
                        worker_id=worker.worker_id,
                        error=str(exc),
                    )
                last_process_heartbeat = now
            while len(active) < concurrency:
                active.add(executor.submit(worker.tick))
            done, active = wait(active, timeout=poll_seconds, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    result = future.result()
                except Exception as exc:  # defensive loop isolation
                    logger.exception("semantic_scene_image_worker_tick_failed", error=str(exc))
                    continue
                if result.get("action") == "not_claimed":
                    # Avoid a hot empty-queue loop while retaining sub-second pickup.
                    time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
