"""Durable bounded-concurrency worker for one Semantic UGC image per script."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import os
import time
from types import SimpleNamespace
from typing import Any, Optional

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.features.semantic_videos import queries
from app.features.semantic_videos.handlers import generate_candidates


logger = get_logger(__name__)
DEFAULT_CONCURRENCY = 2
DEFAULT_LEASE_SECONDS = 600


class SemanticSceneImageWorker:
    def __init__(
        self,
        *,
        repo: Any = queries,
        worker_id: Optional[str] = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        if lease_seconds < 30 or lease_seconds > 900:
            raise ValidationError("Scene-image lease must be between 30 and 900 seconds.")
        self.repo = repo
        self.worker_id = worker_id or f"semantic-scene-image-v1-{os.getpid()}"
        self.lease_seconds = lease_seconds

    def tick(self) -> dict[str, Any]:
        job = self.repo.claim_scene_image_job(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if not job:
            return {"action": "not_claimed"}

        job_id = str(job["id"])
        lease_token = str(job["lease_token"])
        post_id = str(job["post_id"])
        request = SimpleNamespace(
            state=SimpleNamespace(
                user_email=str(job.get("requested_by") or "scene-image-worker"),
                correlation_id=str(job.get("correlation_id") or job_id),
            )
        )
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
            self.repo.finish_scene_image_job(
                job_id=job_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                status="completed",
                run_id=run_id,
            )
            logger.info(
                "semantic_scene_image_completed",
                job_id=job_id,
                post_id=post_id,
                run_id=run_id,
            )
            return {"action": "completed", "job_id": job_id, "run_id": run_id}
        except Exception as exc:  # noqa: BLE001
            error = {"code": type(exc).__name__, "message": str(exc)[:500]}
            try:
                self.repo.finish_scene_image_job(
                    job_id=job_id,
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                    status="failed",
                    error=error,
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
    with ThreadPoolExecutor(
        max_workers=concurrency,
        thread_name_prefix="semantic-scene-image",
    ) as executor:
        while True:
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
