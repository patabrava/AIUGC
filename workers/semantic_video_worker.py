"""Lease-fenced Semantic UGC worker with fail-closed paid submissions.

The worker performs one provider wave or one post-generation stage per tick. All
paid state transitions are delegated to transaction-safe repository RPCs so a
crash can never be guessed safe to resubmit.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any, Callable, Mapping, Optional, Sequence

import httpx

from app.adapters.storage_client import get_storage_client
from app.adapters.vertex_ai_client import VertexAIClient
from app.core.errors import StateTransitionError, ThirdPartyError, ValidationError
from app.core.logging import get_logger
from app.features.semantic_videos import queries
from app.features.semantic_videos.qa_policy import (
    acoustic_qa_requires_localized_paid_retry,
)
from app.features.batches.state_machine import reconcile_batch_video_pipeline_state
from app.features.semantic_videos.visual_contract import (
    build_actor_reference_fingerprint,
    validate_approved_scene_plate_identity,
    validate_scene_plate_generation_contract,
    validate_visual_contract,
)
from app.features.shot_production.audio_seams import MAX_EXACT_DELIVERY_RETIME_RATIO
from app.features.shot_production.duration import (
    SEMANTIC_TERMINAL_SPEECH_GUARD_SECONDS,
    build_semantic_duration_contract,
    semantic_terminal_speech_cut_floor,
)
from app.features.shot_production.runner import load_video_uri
from app.features.shot_production.shot_deck import derive_shot_deck


logger = get_logger(__name__)
DEFAULT_MAX_INFLIGHT = 2
DEFAULT_WORKER_CONCURRENCY = 2
MAX_WORKER_CONCURRENCY = 4
DEFAULT_LEASE_SECONDS = 120
DEFAULT_HEARTBEAT_SECONDS = 40.0
DEFAULT_WORKSPACE_RETENTION_SECONDS = 1800
DEFAULT_WORKSPACE_MAX_COUNT = 4
EXECUTABLE_STAGES = frozenset(
    {
        "generating",
        "transcript_qa",
        "identity_qa",
        "voice_qa",
        "acoustic_qa",
        "composing",
        "uploading",
    }
)
NEXT_STAGE = {
    "transcript_qa": "identity_qa",
    "identity_qa": "voice_qa",
    "voice_qa": "acoustic_qa",
    "acoustic_qa": "composing",
    "composing": "uploading",
}


@dataclass(frozen=True)
class WorkerTickResult:
    run_id: Optional[str]
    stage: Optional[str]
    action: str
    processed: int = 0


class SemanticVideoRepository:
    """Thin adapter around the Semantic UGC persistence functions."""

    def claim_run(self, *, run_id: Optional[str], worker_id: str, lease_seconds: int):
        return queries.acquire_run_lease(
            run_id=run_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )

    def list_attempts(self, run_id: str):
        return queries.list_attempts(run_id)

    def reserve_submission(self, **kwargs):
        return queries.reserve_paid_submission(**kwargs)

    def persist_worker_exception(self, **kwargs):
        return queries.persist_worker_exception(**kwargs)

    def persist_submission_intent(self, **kwargs):
        return queries.persist_worker_submission_intent(**kwargs)

    def persist_accepted_operation(self, **kwargs):
        return queries.persist_worker_accepted_operation(**kwargs)

    def persist_submission_unknown(self, **kwargs):
        return queries.persist_worker_submission_unknown(**kwargs)

    def persist_provider_failure(self, **kwargs):
        return queries.persist_worker_provider_failure(**kwargs)

    def persist_completed_take(self, **kwargs):
        return queries.persist_worker_completed_take(**kwargs)

    def advance_stage(self, **kwargs):
        return queries.advance_worker_stage(**kwargs)

    def require_retry_approval(self, **kwargs):
        return queries.require_worker_retry_approval(**kwargs)

    def complete_run(self, **kwargs):
        return queries.complete_worker_run(**kwargs)

    def reconcile_batch_state(self, *, batch_id: str, correlation_id: str):
        return reconcile_batch_video_pipeline_state(
            batch_id=batch_id,
            correlation_id=correlation_id,
        )

    def release_run(self, **kwargs):
        return queries.release_worker_lease(**kwargs)

    def renew_run(self, **kwargs):
        return queries.renew_worker_lease(**kwargs)


class _LeaseHeartbeat:
    """Renew one fenced worker lease while a blocking stage is in progress."""

    def __init__(
        self,
        *,
        repo: Any,
        run_id: str,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
        interval_seconds: float,
    ) -> None:
        self.repo = repo
        self.run_id = run_id
        self.worker_id = worker_id
        self.lease_token = lease_token
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"semantic-video-lease-{run_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=min(10.0, max(1.0, self.lease_seconds / 4)))
        if self._thread.is_alive():
            logger.warning(
                "semantic_video_lease_heartbeat_stop_timeout",
                run_id=self.run_id,
                worker_id=self.worker_id,
            )

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                renewed = self.repo.renew_run(
                    run_id=self.run_id,
                    worker_id=self.worker_id,
                    lease_token=self.lease_token,
                    lease_seconds=self.lease_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "semantic_video_lease_renewal_failed",
                    run_id=self.run_id,
                    worker_id=self.worker_id,
                    error=str(exc),
                )
                continue
            logger.info(
                "semantic_video_lease_renewed",
                run_id=self.run_id,
                worker_id=self.worker_id,
                lease_expires_at=(renewed or {}).get("lease_expires_at"),
            )


class ProductionStageRunner:
    """Bridge persisted runs into the existing audited shot-production pipeline."""

    _MANIFEST_GLOBAL_KEYS = (
        "contact_sheet",
        "actor_identity_qa",
        "scene_continuity_qa",
        "visual_qa",
        "voice_qa",
        "stitch",
        "final_transcript",
        "final_transcript_qa",
        "seam_qa",
        "composition_history",
        "seam_repair_history",
        "acoustic_seam_plan",
        "acoustic_seam_qa",
        "delivery_visual_qa",
        "delivery_terminal_qa",
        "delivery_review_advisories",
        "caption",
        "media_qa",
        "upload_intent",
        "upload",
        "upload_verification",
    )

    def __init__(
        self,
        *,
        storage: Optional[Any] = None,
        deepgram: Optional[Any] = None,
        work_root: Optional[Path] = None,
        workspace_retention_seconds: Optional[int] = None,
        workspace_max_count: Optional[int] = None,
    ) -> None:
        self.storage = storage or get_storage_client()
        self.deepgram = deepgram
        self.work_root = Path(
            work_root or os.getenv("SEMANTIC_VIDEO_WORK_ROOT", "/tmp/semantic-video-worker")
        )
        self.workspace_retention_seconds = max(
            60,
            int(
                workspace_retention_seconds
                if workspace_retention_seconds is not None
                else os.getenv(
                    "SEMANTIC_VIDEO_WORKSPACE_RETENTION_SECONDS",
                    str(DEFAULT_WORKSPACE_RETENTION_SECONDS),
                )
            ),
        )
        self.workspace_max_count = max(
            2,
            int(
                workspace_max_count
                if workspace_max_count is not None
                else os.getenv(
                    "SEMANTIC_VIDEO_WORKSPACE_MAX_COUNT",
                    str(DEFAULT_WORKSPACE_MAX_COUNT),
                )
            ),
        )

    def run_stage(
        self,
        *,
        stage: str,
        run: Mapping[str, Any],
        takes: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if stage == "uploading":
            return self._project_delivery(run)
        if stage == "composing":
            delivery = self._delivery(run)
            return {
                "passed": True,
                "artifacts": {
                    **dict(run.get("artifact_manifest") or {}),
                    "composition": {"passed": True, "source": "checksum_verified_delivery"},
                    "delivery": delivery,
                },
            }
        if stage not in {"transcript_qa", "identity_qa", "voice_qa", "acoustic_qa"}:
            raise ValidationError("Unsupported semantic video production stage.", {"stage": stage})

        manifest_path = self._materialize_manifest(run, takes)
        if stage != "transcript_qa":
            self._repair_accepted_transcript_timing(manifest_path)
        try:
            if stage == "transcript_qa":
                pipeline = self._runner()
                pipeline.transcribe_and_validate_takes(manifest_path, self._deepgram())
            elif stage == "identity_qa":
                pipeline = self._runner()
                pipeline.build_contact_sheet(manifest_path)
                pipeline.run_visual_qa(manifest_path)
                identity_payload = self._read_manifest(manifest_path)
                contact = identity_payload.get("contact_sheet")
                if not isinstance(contact, Mapping):
                    raise ValidationError(
                        "Semantic video identity QA did not persist a contact sheet."
                    )
                contact_path = Path(str(contact.get("path") or ""))
                contact_bytes = contact_path.read_bytes()
                contact_hash = sha256(contact_bytes).hexdigest()
                if (
                    contact_hash != str(contact.get("sha256") or "")
                    or len(contact_bytes) != int(contact.get("bytes") or -1)
                ):
                    raise StateTransitionError(
                        "Semantic video identity contact sheet changed before publication."
                    )
                contact_upload = self.storage.upload_image(
                    image_bytes=contact_bytes,
                    file_name=f"semantic-video-{run['id']}-identity-{contact_hash}.jpg",
                    correlation_id=f"semantic_ugc_{run['id']}_identity_contact",
                    content_type="image/jpeg",
                )
                identity_payload["contact_sheet"] = {
                    **dict(contact),
                    "storage_uri": str(contact_upload.get("url") or ""),
                    "storage_key": str(contact_upload.get("storage_key") or ""),
                }
                pipeline._atomic_write_json(manifest_path, identity_payload)  # noqa: SLF001
                logger.info(
                    "semantic_video_identity_qa_completed",
                    run_id=str(run["id"]),
                    contact_sha256=contact_hash[:12],
                    actor_identity_passed=bool(
                        (identity_payload.get("actor_identity_qa") or {}).get("passed")
                    ),
                    actor_identity_confidence=(
                        identity_payload.get("actor_identity_qa") or {}
                    ).get("confidence"),
                    scene_continuity_passed=bool(
                        (identity_payload.get("scene_continuity_qa") or {}).get("passed")
                    ),
                    scene_continuity_confidence=(
                        identity_payload.get("scene_continuity_qa") or {}
                    ).get("confidence"),
                )
            elif stage == "voice_qa":
                self._runner().run_voice_qa(manifest_path)
            else:
                return self._compose_upload_delivery(run, takes, manifest_path)
        except ValidationError as exc:
            return self._qa_failure(stage, manifest_path, takes, exc)

        payload = self._read_manifest(manifest_path)
        report_key = {
            "transcript_qa": "transcript_qa",
            "identity_qa": "visual_qa",
            "voice_qa": "voice_qa",
        }[stage]
        if report_key == "transcript_qa":
            report: Any = [take.get("transcript_qa") for take in payload["takes"]]
        else:
            report = payload.get(report_key)
        return {
            "passed": True,
            "artifacts": {
                "pipeline_manifest": payload,
                stage: report,
            },
        }

    def accept_transcript_advisory(
        self,
        *,
        run: Mapping[str, Any],
        takes: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Persist the operator's $0 transcript-review override for later stages."""
        manifest_path = self._materialize_manifest(run, takes)
        payload = self._read_manifest(manifest_path)
        reviewed_indexes: list[int] = []
        for take in payload.get("takes") or []:
            transcript_qa = take.get("transcript_qa")
            if not isinstance(transcript_qa, dict) or transcript_qa.get("passed") is True:
                continue
            transcript = take.get("transcript")
            words = (
                transcript.get("words")
                if isinstance(transcript, Mapping)
                else None
            )
            if (
                not isinstance(words, list)
                or not words
                or not isinstance(words[0], Mapping)
                or not isinstance(words[-1], Mapping)
            ):
                raise StateTransitionError(
                    "Transcript advisory resume requires persisted word timestamps.",
                    {"take_index": take.get("index")},
                )
            try:
                first_word_start = float(words[0].get("start"))
                final_word_end = float(words[-1].get("end"))
                duration_seconds = float(take.get("duration_seconds"))
            except (TypeError, ValueError) as exc:
                raise StateTransitionError(
                    "Transcript advisory resume requires valid word timestamps.",
                    {"take_index": take.get("index")},
                ) from exc
            if (
                not math.isfinite(first_word_start)
                or not math.isfinite(final_word_end)
                or not math.isfinite(duration_seconds)
                or first_word_start < 0
                or final_word_end < first_word_start
                or duration_seconds <= 0
            ):
                raise StateTransitionError(
                    "Transcript advisory resume requires valid word timestamps.",
                    {"take_index": take.get("index")},
                )
            transcript_qa["automated_passed"] = False
            transcript_qa["manual_review_accepted"] = True
            transcript_qa["passed"] = True
            transcript_qa["first_word_start_seconds"] = first_word_start
            transcript_qa["final_word_end_seconds"] = min(
                duration_seconds,
                final_word_end,
            )
            take["trim_window"] = {
                "start_seconds": (
                    max(0.0, first_word_start - 0.25)
                    if int(take["index"]) > 0
                    else 0.0
                ),
                "end_seconds": min(
                    duration_seconds,
                    final_word_end + 0.25,
                ),
                "source": "deepgram_word_window",
            }
            take["status"] = "transcribed"
            reviewed_indexes.append(int(take["index"]))
        if not reviewed_indexes:
            raise StateTransitionError(
                "Transcript advisory resume requires persisted failed transcript evidence."
            )
        payload["status"] = "transcript_qa_passed"
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "passed": True,
            "artifacts": {
                "pipeline_manifest": payload,
                "transcript_qa": [
                    take.get("transcript_qa") for take in payload.get("takes") or []
                ],
                "transcript_manual_review": {
                    "accepted": True,
                    "take_indexes": reviewed_indexes,
                },
            },
        }

    def _repair_accepted_transcript_timing(self, manifest_path: Path) -> None:
        """Rebuild missing legacy word windows without resubmitting paid video."""
        payload = self._read_manifest(manifest_path)
        repair_indexes = {
            int(take.get("index") or 0)
            for take in payload.get("takes") or []
            if isinstance(take, Mapping)
            and isinstance(take.get("transcript_qa"), Mapping)
            and take["transcript_qa"].get("manual_review_accepted") is True
            and (
                take["transcript_qa"].get("first_word_start_seconds") is None
                or take["transcript_qa"].get("final_word_end_seconds") is None
                or (take.get("trim_window") or {}).get("source")
                != "deepgram_word_window"
            )
        }
        if not repair_indexes:
            return

        pipeline = self._runner()
        try:
            pipeline.transcribe_and_validate_takes(manifest_path, self._deepgram())
        except ValidationError:
            # The operator already accepted the transcript mismatch. This pass
            # exists only to recover the missing Deepgram timing evidence.
            pass

        payload = self._read_manifest(manifest_path)
        for take in payload.get("takes") or []:
            index = int(take.get("index") or 0)
            if index not in repair_indexes:
                continue
            transcript = take.get("transcript")
            words = transcript.get("words") if isinstance(transcript, Mapping) else None
            if (
                not isinstance(words, list)
                or not words
                or not isinstance(words[0], Mapping)
                or not isinstance(words[-1], Mapping)
            ):
                raise StateTransitionError(
                    "Accepted transcript timing repair requires Deepgram word evidence.",
                    {"take_index": index},
                )
            duration_seconds = float(take.get("duration_seconds") or 0)
            first_word_start = max(0.0, float(words[0].get("start") or 0.0))
            final_word_end = min(
                duration_seconds,
                float(words[-1].get("end") or 0.0),
            )
            if (
                not math.isfinite(duration_seconds)
                or not math.isfinite(first_word_start)
                or not math.isfinite(final_word_end)
                or duration_seconds <= 0
                or final_word_end <= first_word_start
            ):
                raise StateTransitionError(
                    "Accepted transcript timing repair produced an invalid word window.",
                    {"take_index": index},
                )
            transcript_qa = take.get("transcript_qa")
            if not isinstance(transcript_qa, dict):
                transcript_qa = {}
                take["transcript_qa"] = transcript_qa
            transcript_qa["automated_passed"] = transcript_qa.get("passed") is True
            transcript_qa["manual_review_accepted"] = True
            transcript_qa["passed"] = True
            transcript_qa["first_word_start_seconds"] = first_word_start
            transcript_qa["final_word_end_seconds"] = final_word_end
            take["trim_window"] = {
                "start_seconds": (
                    max(0.0, first_word_start - 0.25) if index > 0 else 0.0
                ),
                "end_seconds": min(duration_seconds, final_word_end + 0.25),
                "source": "deepgram_word_window",
            }
            take["status"] = "transcribed"
        payload["status"] = "transcript_qa_passed"
        pipeline._atomic_write_json(manifest_path, payload)  # noqa: SLF001
        logger.warning(
            "semantic_video_accepted_transcript_timing_repaired",
            run_id=str(payload.get("run_id") or ""),
            take_indexes=sorted(repair_indexes),
        )

    @staticmethod
    def _apply_downstream_qa_advisory(
        payload: dict[str, Any],
        qa_advisory: Any,
    ) -> None:
        """Convert an advisory evaluator result into an explicit accepted gate."""
        if (
            isinstance(qa_advisory, Mapping)
            and qa_advisory.get("required") is True
            and qa_advisory.get("stage") == "acoustic_qa"
        ):
            payload["delivery_qa_advisory"] = dict(qa_advisory)
            if qa_advisory.get("accept_existing_delivery_as_is") is not True:
                return
            accepted_by = "operator_accept_existing_delivery_as_is"
            fallback_message = "Operator accepted the existing generated delivery."
        elif (
            not isinstance(qa_advisory, Mapping)
            or qa_advisory.get("required") is not True
            or qa_advisory.get("stage") != "identity_qa"
        ):
            return
        else:
            accepted_by = "paid_generated_take_qa_advisory"
            fallback_message = "Identity QA service unavailable."
        report = payload.get("visual_qa")
        if not isinstance(report, dict):
            report = {
                "passed": False,
                "status": "qa_service_unavailable",
                "blocking_reasons": [
                    str(qa_advisory.get("message") or fallback_message)
                ],
                "observed_differences": [],
            }
            payload["visual_qa"] = report
        elif report.get("passed") is not False:
            return
        report["provider_passed"] = False
        report["provider_blocking_reasons"] = list(
            report.get("blocking_reasons") or []
        )
        report["manual_review_accepted"] = True
        report["accepted_by"] = accepted_by
        report["passed"] = True
        report["blocking_reasons"] = []
        payload["status"] = "visual_qa_passed"

    @staticmethod
    def _runner():
        from app.features.shot_production import runner

        return runner

    def _deepgram(self):
        if self.deepgram is None:
            from app.adapters.deepgram_client import get_deepgram_client

            self.deepgram = get_deepgram_client()
        return self.deepgram

    def _prepare_workspace(self, run_id: str) -> Path:
        """Bound disposable local artifacts before materializing the active run."""
        self.work_root.mkdir(parents=True, exist_ok=True)
        active = self.work_root / run_id
        now = time.time()
        candidates: list[tuple[float, Path]] = []
        for path in self.work_root.iterdir():
            if path == active or not path.is_dir():
                continue
            try:
                modified_at = path.stat().st_mtime
            except FileNotFoundError:
                continue
            candidates.append((modified_at, path))

        candidates.sort(key=lambda item: item[0])
        excess_count = max(
            0,
            len(candidates) - (self.workspace_max_count - 1),
        )
        stale_cutoff = now - self.workspace_retention_seconds
        reclaimed: list[str] = []
        for position, (modified_at, path) in enumerate(candidates):
            if modified_at > stale_cutoff and position >= excess_count:
                continue
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                continue
            reclaimed.append(path.name)
        if reclaimed:
            logger.info(
                "semantic_video_workspaces_reclaimed",
                active_run_id=run_id,
                reclaimed_count=len(reclaimed),
                reclaimed_run_ids=reclaimed,
            )

        active.mkdir(parents=True, exist_ok=True)
        os.utime(active, None)
        return active

    def cleanup_run_workspace(self, run_id: str) -> None:
        """Remove a delivered run's rematerializable local workspace."""
        run_dir = self.work_root / str(run_id)
        try:
            shutil.rmtree(run_dir)
        except FileNotFoundError:
            return
        logger.info(
            "semantic_video_workspace_removed",
            run_id=str(run_id),
        )

    def _materialize_manifest(
        self,
        run: Mapping[str, Any],
        takes: Sequence[Mapping[str, Any]],
    ) -> Path:
        pipeline = self._runner()
        run_id = str(run["id"])
        run_dir = self._prepare_workspace(run_id)
        raw_dir = run_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "manifest.json"

        master = run.get("master_snapshot")
        if not isinstance(master, Mapping):
            raise ValidationError("Semantic video production requires an approved master snapshot.")
        master_bytes = self.storage.download_video(
            video_url=str(master.get("storage_uri") or ""),
            correlation_id=f"semantic_ugc_{run_id}_stage_master",
        )
        master_hash = str(run.get("master_hash") or master.get("sha256") or "")
        if (
            sha256(master_bytes).hexdigest() != master_hash
            or int(master.get("byte_length") or -1) != len(master_bytes)
        ):
            raise StateTransitionError("Semantic video approved master changed during production.")
        master_path = run_dir / "approved-master.png"
        master_path.write_bytes(master_bytes)

        reference_snapshot = run.get("reference_snapshot")
        actor_rows = (
            reference_snapshot.get("actor_references")
            if isinstance(reference_snapshot, Mapping)
            else None
        )
        if not isinstance(actor_rows, list) or len(actor_rows) != 2:
            raise StateTransitionError(
                "Semantic video production requires two immutable actor references."
            )
        actor_dir = run_dir / "actor-references"
        actor_dir.mkdir(parents=True, exist_ok=True)
        actor_references = []
        for row, expected_role in zip(
            actor_rows, ("actor_front", "actor_three_quarter")
        ):
            if not isinstance(row, Mapping) or row.get("role") != expected_role:
                raise StateTransitionError(
                    "Semantic video actor reference order changed during production."
                )
            actor_bytes = self.storage.download_video(
                video_url=str(row.get("storage_uri") or ""),
                correlation_id=f"semantic_ugc_{run_id}_{expected_role}",
            )
            actor_hash = sha256(actor_bytes).hexdigest()
            if (
                actor_hash != str(row.get("sha256") or "")
                or len(actor_bytes) != int(row.get("byte_length") or -1)
                or not str(row.get("mime_type") or "").startswith("image/")
            ):
                raise StateTransitionError(
                    "Semantic video original actor reference changed during production.",
                    {"role": expected_role},
                )
            suffix = ".jpg" if str(row.get("mime_type")) == "image/jpeg" else ".png"
            actor_path = actor_dir / f"{expected_role}-{actor_hash}{suffix}"
            actor_path.write_bytes(actor_bytes)
            actor_references.append(
                {
                    "role": expected_role,
                    "path": str(actor_path),
                    "storage_uri": str(row.get("storage_uri") or ""),
                    "mime_type": str(row.get("mime_type") or ""),
                    "byte_length": len(actor_bytes),
                    "sha256": actor_hash,
                }
            )

        artifact_manifest = run.get("artifact_manifest")
        artifacts = dict(artifact_manifest) if isinstance(artifact_manifest, Mapping) else {}
        prior = artifacts.get("pipeline_manifest")
        prior_manifest = dict(prior) if isinstance(prior, Mapping) else {}
        qa_advisory = artifacts.get("qa_advisory")
        transcript_advisory_was_accepted = (
            str(run.get("stage") or "") != "transcript_qa"
            and prior_manifest.get("status") == "transcript_qa_passed"
            and isinstance(qa_advisory, Mapping)
            and qa_advisory.get("required") is True
            and qa_advisory.get("stage") == "transcript_qa"
        )
        prior_takes = {
            (int(take.get("index") or 0), int(take.get("attempt") or 1)): take
            for take in prior_manifest.get("takes") or []
            if isinstance(take, Mapping)
        }

        ordered = sorted(takes, key=lambda item: int(item["take_index"]))
        manifest_takes = []
        for take in ordered:
            index = int(take["take_index"])
            attempt = int(take.get("attempt") or 1)
            raw_uri = str(take.get("raw_artifact_uri") or "")
            raw_hash = str(take.get("raw_artifact_sha256") or "")
            if not raw_uri or not re.fullmatch(r"[0-9a-f]{64}", raw_hash):
                raise StateTransitionError(
                    "Semantic video QA requires a checksum-addressed raw take.",
                    {"take_index": index},
                )
            raw_bytes = self.storage.download_video(
                video_url=raw_uri,
                correlation_id=f"semantic_ugc_{run_id}_stage_take_{index}",
            )
            if sha256(raw_bytes).hexdigest() != raw_hash:
                raise StateTransitionError(
                    "Semantic video raw take changed during production.",
                    {"take_index": index},
                )
            raw_path = raw_dir / f"take-{index}-attempt-{attempt}-{raw_hash}.mp4"
            raw_path.write_bytes(raw_bytes)

            contract = take.get("request_contract")
            transform = take.get("shot_transform")
            if not isinstance(contract, Mapping) or not isinstance(transform, Mapping):
                raise StateTransitionError("Semantic video take contract is incomplete.")
            previous = prior_takes.get((index, attempt), {})
            row = {
                "index": index,
                "attempt": attempt,
                "attempt_history": [],
                "status": "raw_completed",
                "beat": {
                    "index": index,
                    "text": str(take.get("beat_text") or ""),
                    "word_count": int(take.get("word_count") or 0),
                    "estimated_speech_seconds": float(take.get("estimated_speech_seconds") or 0),
                    "provider_duration_seconds": int(take.get("provider_duration_seconds") or 0),
                },
                "shot": {
                    "name": str(transform.get("name") or f"take-{index}"),
                    "path": "",
                    "source_sha256": str(transform.get("source_sha256") or master_hash),
                    "sha256": str(transform.get("output_sha256") or ""),
                    "crop_box": list(transform.get("crop_box") or []),
                    "width": int(transform.get("width") or 0),
                    "height": int(transform.get("height") or 0),
                    "mime_type": str(transform.get("mime_type") or "image/png"),
                },
                "model": str(take.get("provider_model") or contract.get("provider_model") or ""),
                "aspect_ratio": str(contract.get("aspect_ratio") or "9:16"),
                "duration_seconds": int(take.get("provider_duration_seconds") or 0),
                "seed": take.get("seed"),
                "prompt": str(contract.get("prompt") or ""),
                "negative_prompt": str(contract.get("negative_prompt") or ""),
                "submission": {"state": "accepted"},
                "operation": {"operation_id": take.get("operation_id")},
                "raw": {
                    "path": str(raw_path),
                    "sha256": raw_hash,
                    "bytes": len(raw_bytes),
                    "provider_video_uri": take.get("provider_video_uri"),
                    "storage_uri": raw_uri,
                },
                "transcript": previous.get("transcript"),
                "transcript_qa": deepcopy_rows(
                    [previous.get("transcript_qa")]
                )[0]
                if isinstance(previous.get("transcript_qa"), Mapping)
                else None,
                "trim_window": previous.get("trim_window"),
            }
            transcript_qa = row.get("transcript_qa")
            manual_review_was_persisted = (
                isinstance(transcript_qa, dict)
                and transcript_qa.get("manual_review_accepted") is True
            )
            if (
                (transcript_advisory_was_accepted or manual_review_was_persisted)
                and isinstance(transcript_qa, dict)
            ):
                transcript_qa.setdefault(
                    "automated_passed",
                    transcript_qa.get("passed") is True,
                )
                transcript_qa["manual_review_accepted"] = True
                transcript_qa["passed"] = True
                row["status"] = "transcribed"
                transcript = row.get("transcript")
                words = (
                    transcript.get("words")
                    if isinstance(transcript, Mapping)
                    else None
                )
                if (
                    transcript_qa.get("first_word_start_seconds") is None
                    and isinstance(words, list)
                    and words
                    and isinstance(words[0], Mapping)
                ):
                    transcript_qa["first_word_start_seconds"] = float(
                        words[0].get("start") or 0.0
                    )
                if not row.get("trim_window"):
                    first_word_start = transcript_qa.get(
                        "first_word_start_seconds"
                    )
                    final_word_end = transcript_qa.get(
                        "final_word_end_seconds"
                    )
                    if first_word_start is not None and final_word_end is not None:
                        row["trim_window"] = {
                            "start_seconds": (
                                max(0.0, float(first_word_start) - 0.25)
                                if index > 0
                                else 0.0
                            ),
                            "end_seconds": min(
                                float(row["duration_seconds"]),
                                float(final_word_end) + 0.25,
                            ),
                            "source": "deepgram_word_window",
                        }
            manifest_takes.append(row)

        requested_duration = int(run.get("requested_duration_seconds") or 0)
        script_snapshot = run.get("script_snapshot")
        script = dict(script_snapshot) if isinstance(script_snapshot, Mapping) else {}
        script_text = str(script.get("text") or "")
        canonical_duration = build_semantic_duration_contract(requested_duration)
        delivery_contract = {
            "requested": float(canonical_duration.requested_duration_seconds),
            "minimum": canonical_duration.delivery_min_seconds,
            "maximum": canonical_duration.delivery_max_seconds,
        }
        source = str(script.get("source") or pipeline.APP_SCRIPT_SOURCE)
        manifest_script: dict[str, Any] = {
            "path": "",
            "input_sha256": str(run.get("script_hash") or ""),
            "text_sha256": sha256(script_text.encode("utf-8")).hexdigest(),
            "source": source,
            "category": "semantic_ugc",
            "creation_mode": str(script.get("creation_mode") or "semantic_ugc"),
            "script_review_status": str(
                script.get("script_review_status")
                or script.get("review_status")
                or ""
            ),
            "planning_profile": pipeline.PLANNING_PROFILE,
            "delivery_duration_seconds": delivery_contract,
            "text": script_text,
            "planned_provider_durations": [
                take["duration_seconds"] for take in manifest_takes
            ],
            "source_payload": {**script, "source": source},
        }
        if source in {
            pipeline.SEMANTIC_SCRIPT_SOURCE,
            pipeline.MANUAL_SEMANTIC_SCRIPT_SOURCE,
        }:
            manifest_script["target_duration_seconds"] = requested_duration
        else:
            # Backward-compatible reconstruction for runs approved before semantic
            # provenance was snapshotted.
            manifest_script["target_length_tier"] = requested_duration
        payload: dict[str, Any] = {
            "version": pipeline.MANIFEST_VERSION,
            "run_id": run_id,
            "created_at": str(run.get("created_at") or ""),
            "updated_at": str(run.get("updated_at") or ""),
            "status": "raw_completed",
            "base_seed": min((int(take.get("seed") or 0) for take in ordered), default=0),
            "approved_master": {
                "path": str(master_path),
                "sha256": master_hash,
                "mime_type": str(master.get("mime_type") or "image/png"),
            },
            "actor_references": actor_references,
            "script": manifest_script,
            "takes": manifest_takes,
        }
        for key in self._MANIFEST_GLOBAL_KEYS:
            if key in prior_manifest:
                payload[key] = prior_manifest[key]
        self._apply_downstream_qa_advisory(payload, qa_advisory)
        payload["request_contract_sha256"] = pipeline._canonical_sha256(  # noqa: SLF001
            pipeline._request_contract_payload(payload)  # noqa: SLF001
        )
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return manifest_path

    @staticmethod
    def _read_manifest(manifest_path: Path) -> dict[str, Any]:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise StateTransitionError("Semantic video pipeline manifest is invalid.")
        return payload

    def _qa_failure(
        self,
        stage: str,
        manifest_path: Path,
        takes: Sequence[Mapping[str, Any]],
        exc: ValidationError,
    ) -> dict[str, Any]:
        payload = self._read_manifest(manifest_path)
        quality_failure_statuses = {
            "transcript_failed",
            "visual_qa_failed",
            "voice_qa_failed",
            "acoustic_plan_failed",
            "acoustic_seam_qa_failed",
            "delivery_visual_qa_failed",
            "final_transcript_failed",
            "seam_qa_failed",
            "media_qa_failed",
        }
        if str(payload.get("status") or "") not in quality_failure_statuses:
            raise exc
        failed: list[int] = []
        if stage == "transcript_qa":
            failed = [
                int(take["index"])
                for take in payload.get("takes") or []
                if not (take.get("transcript_qa") or {}).get("passed")
            ]
        elif stage == "voice_qa":
            failed = [int(index) for index in (payload.get("voice_qa") or {}).get("outlier_take_indexes") or []]
        elif stage == "acoustic_qa":
            failed = [
                int(index)
                for index in (payload.get("delivery_visual_qa") or {}).get(
                    "recommended_retry_take_indexes"
                )
                or (payload.get("acoustic_seam_qa") or {}).get(
                    "recommended_retry_take_indexes"
                )
                or (payload.get("acoustic_plan_failure") or {}).get(
                    "recommended_retry_take_indexes"
                )
                or []
            ]
        if not failed:
            failed = [int(take["take_index"]) for take in takes]
        failed = sorted(set(failed))
        qa_failure = {
            "stage": stage,
            "message": exc.message,
            "details": exc.details,
            "failed_take_indexes": failed,
        }
        if stage == "acoustic_qa":
            acoustic_plan_failure = payload.get("acoustic_plan_failure")
            if acoustic_qa_requires_localized_paid_retry(qa_failure, payload):
                if isinstance(acoustic_plan_failure, Mapping):
                    failure_type = "acoustic_plan_failure"
                elif payload.get("status") == "seam_qa_failed":
                    failure_type = "seam_repair_exhausted"
                else:
                    failure_type = "delivery_visual_regeneration"
                qa_failure["failure_type"] = failure_type
                qa_failure["retry_mode"] = "localized_paid_take"
        return {
            "passed": False,
            "failed_take_indexes": failed,
            "artifacts": {
                "pipeline_manifest": payload,
                "qa_failure": qa_failure,
                "guidance": (
                    f"Regenerate only the failed semantic beat and correct the {stage} evidence: "
                    f"{exc.message}"
                ),
            },
        }

    def _compose_upload_delivery(
        self,
        run: Mapping[str, Any],
        takes: Sequence[Mapping[str, Any]],
        manifest_path: Path,
    ) -> dict[str, Any]:
        pipeline = self._runner()
        try:
            pipeline.compose_and_caption(
                manifest_path,
                self._deepgram(),
                acoustic_seams=len(takes) > 1,
                operator_review_delivery=True,
            )
        except ValidationError:
            failed_payload = self._read_manifest(manifest_path)
            repair_history = failed_payload.get("seam_repair_history")
            if (
                str(failed_payload.get("status") or "") != "seam_qa_failed"
                or (isinstance(repair_history, list) and repair_history)
            ):
                raise
            seam_report = failed_payload.get("seam_qa") or {}
            pipeline.repair_failed_seam_windows(
                manifest_path,
                reason=(
                    "Automatically tighten the checksum-verified stitched seam after "
                    f"final transcript QA measured gaps {seam_report.get('gaps_seconds') or []}."
                ),
            )
            logger.info(
                "semantic_video_seam_windows_repaired",
                run_id=str(run["id"]),
                failed_seam_indexes=seam_report.get("failed_seam_indexes") or [],
                gaps_seconds=seam_report.get("gaps_seconds") or [],
            )
            pipeline.compose_and_caption(
                manifest_path,
                self._deepgram(),
                acoustic_seams=len(takes) > 1,
                operator_review_delivery=True,
            )
        payload = self._read_manifest(manifest_path)
        review_findings = [
            dict(item)
            for item in payload.get("delivery_review_advisories") or []
            if isinstance(item, Mapping)
        ]
        cleared_acoustic_advisory = self._clear_superseded_acoustic_advisory(payload)
        if cleared_acoustic_advisory:
            pipeline._atomic_write_json(manifest_path, payload)  # noqa: SLF001
        stitch = payload.get("stitch") or {}
        stitch_path = Path(str(stitch.get("path") or ""))
        if not stitch_path.is_file():
            raise StateTransitionError("Semantic video composition did not create the raw final artifact.")
        raw_bytes = stitch_path.read_bytes()
        raw_hash = sha256(raw_bytes).hexdigest()
        raw_key = (
            f"{str(run.get('artifact_prefix') or '').strip('/')}/final/raw/{raw_hash}.mp4"
        )
        raw_upload = self.storage.upload_video(
            video_bytes=raw_bytes,
            file_name=f"{raw_hash}.mp4",
            correlation_id=f"semantic_ugc_{run['id']}_final_raw",
            object_key=raw_key,
        )
        if (
            str(raw_upload.get("storage_key") or "") != raw_key
            or str(raw_upload.get("sha256") or "") != raw_hash
            or int(raw_upload.get("size") or -1) != len(raw_bytes)
        ):
            raise StateTransitionError("Semantic video raw final upload receipt is invalid.")
        caption_upload = pipeline.upload_final(manifest_path, storage_client=self.storage)
        caption_hash = str(caption_upload.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", caption_hash):
            raise StateTransitionError("Semantic video captioned upload receipt is invalid.")
        payload = self._read_manifest(manifest_path)
        delivery = {
            "passed": True,
            "mode": (
                "full_video_operator_review"
                if review_findings
                else "verified_delivery"
            ),
            "raw": {"url": str(raw_upload["url"]), "sha256": raw_hash},
            "captioned": {"url": str(caption_upload["url"]), "sha256": caption_hash},
            "acoustic_status": (
                "evaluated" if len(takes) > 1 else "not_applicable"
            ),
        }
        artifacts = {
            "pipeline_manifest": payload,
            "acoustic_qa": payload.get("acoustic_seam_qa")
            or {"passed": True, "status": "not_applicable"},
            "delivery": delivery,
            # A successful recomposition supersedes any prior retry evidence.
            # Persist JSON null so merge-style stage RPCs clear the stale value.
            "qa_failure": None,
        }
        if cleared_acoustic_advisory:
            artifacts["qa_advisory"] = None
        if review_findings:
            artifacts["qa_advisory"] = {
                "required": True,
                "stage": "delivery_review",
                "message": (
                    "The full video is ready. Automated quality checks flagged "
                    "items for your manual review."
                ),
                "findings": review_findings,
                "paid_retry_required": False,
            }
        return {"passed": True, "artifacts": artifacts}

    @staticmethod
    def _clear_superseded_acoustic_advisory(payload: dict[str, Any]) -> bool:
        advisory = payload.get("delivery_qa_advisory")
        if not isinstance(advisory, Mapping) or advisory.get("stage") != "acoustic_qa":
            return False
        current_reports = (
            payload.get("seam_qa"),
            payload.get("acoustic_seam_qa"),
            payload.get("delivery_visual_qa"),
        )
        if not all(
            isinstance(report, Mapping) and report.get("passed") is True
            for report in current_reports
        ):
            return False
        payload.pop("delivery_qa_advisory", None)
        return True

    def caption_advisory_single_take(
        self,
        *,
        run: Mapping[str, Any],
        takes: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Protect the terminal frame, then burn captions for an advisory delivery."""
        if len(takes) != 1:
            raise StateTransitionError(
                "Advisory caption delivery requires exactly one paid take."
            )

        from app.adapters.caption_aligner import align_transcript_to_script
        from app.adapters.caption_renderer import burn_captions
        from app.adapters.deepgram_client import Word, WordLevelTranscript
        from app.adapters.video_stitcher import stitch_segments
        from app.features.shot_production.duration import (
            SEMANTIC_END_PAN_TAIL_EXCLUSION_SECONDS,
        )
        from app.features.shot_production.visual_seams import (
            evaluate_source_terminal_reset,
        )

        pipeline = self._runner()
        manifest_path = self._materialize_manifest(run, takes)
        payload = self._read_manifest(manifest_path)
        take = payload["takes"][0]
        transcript_payload = take.get("transcript")
        if not isinstance(transcript_payload, Mapping):
            raise StateTransitionError(
                "Advisory caption delivery requires a persisted transcript."
            )
        transcript = pipeline._deserialize_transcript(dict(transcript_payload))  # noqa: SLF001
        aligned = align_transcript_to_script(
            transcript=transcript,
            script=str((payload.get("script") or {}).get("text") or ""),
        )
        if not aligned.words:
            raise StateTransitionError(
                "Advisory caption delivery requires at least one aligned word."
            )

        advisory_window_added = self._ensure_advisory_speech_window(
            take=take,
            transcript=transcript,
        )
        if advisory_window_added:
            pipeline._atomic_write_json(manifest_path, payload)  # noqa: SLF001

        requested_duration = int(run.get("requested_duration_seconds") or 0)
        if requested_duration != 8:
            raise StateTransitionError(
                "Advisory single-take terminal protection requires an 8s delivery."
            )
        artifact_manifest = run.get("artifact_manifest")
        qa_advisory = (
            artifact_manifest.get("qa_advisory")
            if isinstance(artifact_manifest, Mapping)
            else None
        )
        accept_existing_delivery_as_is = bool(
            isinstance(qa_advisory, Mapping)
            and qa_advisory.get("required") is True
            and qa_advisory.get("stage") == "acoustic_qa"
            and qa_advisory.get("accept_existing_delivery_as_is") is True
        )
        trim_window = take.get("trim_window")
        transcript_qa = take.get("transcript_qa")
        if (
            not isinstance(trim_window, Mapping)
            or not isinstance(transcript_qa, Mapping)
            or (
                transcript_qa.get("passed") is not True
                and transcript_qa.get("advisory_delivery_window_verified") is not True
            )
            or trim_window.get("source") != "deepgram_word_window"
        ):
            raise StateTransitionError(
                "Advisory terminal protection requires a verified speech window."
            )
        protected_source_end = (
            float(requested_duration)
            - SEMANTIC_END_PAN_TAIL_EXCLUSION_SECONDS
        )
        transcript_window_end = float(trim_window.get("end_seconds") or 0.0)
        try:
            speech_cut_floor = semantic_terminal_speech_cut_floor(
                transcript_qa.get("final_word_end_seconds")
            )
        except ValueError as exc:
            raise StateTransitionError(str(exc)) from exc
        if not accept_existing_delivery_as_is and speech_cut_floor > protected_source_end + 1e-6:
            raise StateTransitionError(
                "Advisory terminal protection would cut transcript-safe context.",
                {
                    "final_word_end_seconds": float(
                        transcript_qa["final_word_end_seconds"]
                    ),
                    "speech_guard_seconds": SEMANTIC_TERMINAL_SPEECH_GUARD_SECONDS,
                    "speech_cut_floor_seconds": speech_cut_floor,
                    "transcript_window_end_seconds": transcript_window_end,
                    "protected_source_end_seconds": protected_source_end,
                },
            )
        if accept_existing_delivery_as_is:
            speech_cut_floor = float(requested_duration)
        active_cut_retime_ratio = float(requested_duration) / speech_cut_floor
        if (
            not accept_existing_delivery_as_is
            and active_cut_retime_ratio > MAX_EXACT_DELIVERY_RETIME_RATIO + 1e-9
        ):
            raise StateTransitionError(
                "Advisory active-speech cut exceeds the cadence bound.",
                {
                    "failure_type": "terminal_active_speech_timing",
                    "final_word_end_seconds": float(
                        transcript_qa["final_word_end_seconds"]
                    ),
                    "speech_guard_seconds": SEMANTIC_TERMINAL_SPEECH_GUARD_SECONDS,
                    "speech_cut_floor_seconds": speech_cut_floor,
                    "required_retime_ratio": active_cut_retime_ratio,
                    "maximum_retime_ratio": MAX_EXACT_DELIVERY_RETIME_RATIO,
                },
            )

        raw_path = Path(str((take.get("raw") or {}).get("path") or ""))
        if not raw_path.is_file():
            raise StateTransitionError(
                "Advisory caption delivery raw artifact is unavailable."
            )
        raw_bytes = raw_path.read_bytes()
        raw_hash = str(takes[0].get("raw_artifact_sha256") or "")
        if sha256(raw_bytes).hexdigest() != raw_hash:
            raise StateTransitionError(
                "Advisory caption delivery raw checksum changed."
            )

        source_terminal_qa = dict(evaluate_source_terminal_reset(raw_path))
        source_terminal_qa.update(
            {
                "passed": True,
                "source_raw_sha256": raw_hash,
                "take_index": int(take.get("index") or 0),
                "attempt": int(take.get("attempt") or 1),
            }
        )
        stitched_bytes, stitch_metadata = stitch_segments(
            segment_videos=[raw_bytes],
            post_id=str(run["id"]),
            correlation_id=f"semantic_ugc_{run['id']}_advisory_stitch",
            trim_windows=None,
            acoustic_plan=None,
            target_duration_seconds=float(requested_duration),
            terminal_tail_exclusion_seconds=(
                float(requested_duration) - speech_cut_floor
            ),
        )
        stitched_path = manifest_path.parent / "stitched-advisory.mp4"
        stitched_path.write_bytes(stitched_bytes)
        stitched_hash = sha256(stitched_bytes).hexdigest()
        delivery_terminal_qa = dict(
            evaluate_source_terminal_reset(stitched_path)
        )
        delivery_terminal_qa.update(
            {
                "passed": (
                    True
                    if accept_existing_delivery_as_is
                    else not bool(delivery_terminal_qa.get("reset_detected"))
                ),
                "video_sha256": stitched_hash,
                "requires_paid_regeneration": False,
            }
        )
        if accept_existing_delivery_as_is:
            delivery_terminal_qa.update(
                {
                    "provider_passed": not bool(
                        delivery_terminal_qa.get("reset_detected")
                    ),
                    "manual_review_accepted": True,
                    "operator_review_required": True,
                    "accepted_by": "operator_accept_existing_delivery_as_is",
                }
            )
        if delivery_terminal_qa["passed"] is not True:
            raise StateTransitionError(
                "Protected advisory delivery still contains terminal camera drift.",
                {
                    "failure_type": "delivery_terminal_reset",
                    "requires_paid_regeneration": False,
                },
            )

        retime_ratio = float(
            stitch_metadata.get("stitch_end_pan_retime_ratio") or 1.0
        )
        retimed_aligned = WordLevelTranscript(
            words=[
                Word(
                    word=word.word,
                    start=float(word.start) * retime_ratio,
                    end=float(word.end) * retime_ratio,
                )
                for word in aligned.words
            ],
            full_text=aligned.full_text,
        )
        rendered_path = Path(
            burn_captions(
                video_path=str(stitched_path),
                transcript=retimed_aligned,
                correlation_id=f"semantic_ugc_{run['id']}_advisory_captions",
            )
        )
        captioned_bytes = rendered_path.read_bytes()
        caption_hash = sha256(captioned_bytes).hexdigest()
        if caption_hash == raw_hash:
            raise StateTransitionError(
                "Advisory caption renderer returned the unchanged raw artifact."
            )

        captioned_path = manifest_path.parent / "final-captioned.mp4"
        captioned_path.write_bytes(captioned_bytes)
        probe = pipeline._probe_media(captioned_path)  # noqa: SLF001
        duration = build_semantic_duration_contract(
            int(run.get("requested_duration_seconds") or 0)
        )
        media_qa = pipeline.evaluate_final_media_probe(
            probe,
            min_duration_seconds=duration.delivery_min_seconds,
            max_duration_seconds=duration.delivery_max_seconds,
            target_duration_seconds=float(duration.requested_duration_seconds),
        )
        if media_qa.get("passed") is not True:
            raise StateTransitionError(
                "Advisory captioned delivery failed media validation.",
                {"failure_reasons": media_qa.get("failure_reasons") or []},
            )

        object_key = (
            f"{str(run.get('artifact_prefix') or '').strip('/')}/final/"
            f"captioned/{caption_hash}.mp4"
        )
        upload = self.storage.upload_video(
            video_bytes=captioned_bytes,
            file_name=f"{caption_hash}.mp4",
            correlation_id=f"semantic_ugc_{run['id']}_advisory_captioned",
            object_key=object_key,
        )
        if (
            str(upload.get("storage_key") or "") != object_key
            or str(upload.get("sha256") or "") != caption_hash
            or int(upload.get("size") or -1) != len(captioned_bytes)
        ):
            raise StateTransitionError(
                "Advisory captioned upload receipt is invalid."
            )

        payload["caption"] = {
            "captioned_path": str(captioned_path),
            "sha256": caption_hash,
            "bytes": len(captioned_bytes),
            "word_count": len(retimed_aligned.words),
            "aligned_transcript": pipeline._serialize_transcript(retimed_aligned),  # noqa: SLF001
            "probe": probe,
        }
        payload["source_visual_tail_qa"] = {
            "status": "evaluated",
            "passed": True,
            "takes": [source_terminal_qa],
        }
        payload["stitch"] = {
            "path": str(stitched_path),
            "sha256": stitched_hash,
            "metadata": stitch_metadata,
            "probe": pipeline._probe_media(stitched_path),  # noqa: SLF001
        }
        payload["delivery_terminal_qa"] = delivery_terminal_qa
        payload["seam_qa"] = {
            "status": "not_applicable",
            "passed": True,
            "gaps_seconds": [],
        }
        payload["media_qa"] = media_qa
        payload["status"] = "captioned"
        pipeline._atomic_write_json(manifest_path, payload)  # noqa: SLF001
        return {
            "url": str(upload["url"]),
            "sha256": caption_hash,
            "pipeline_manifest": payload,
        }

    @staticmethod
    def _ensure_advisory_speech_window(
        *,
        take: dict[str, Any],
        transcript: Any,
    ) -> bool:
        """Derive a bounded Deepgram speech window for a manual-review delivery."""
        transcript_qa = take.get("transcript_qa")
        trim_window = take.get("trim_window")
        if (
            isinstance(transcript_qa, Mapping)
            and transcript_qa.get("passed") is True
            and isinstance(trim_window, Mapping)
            and trim_window.get("source") == "deepgram_word_window"
        ):
            return False

        words = list(getattr(transcript, "words", ()) or ())
        if not words:
            raise StateTransitionError(
                "Advisory terminal protection requires a verified speech window."
            )
        try:
            first_word_start = float(words[0].start)
            final_word_end = float(words[-1].end)
            duration_seconds = float(take.get("duration_seconds"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise StateTransitionError(
                "Advisory terminal protection requires a verified speech window."
            ) from exc
        if (
            not math.isfinite(first_word_start)
            or not math.isfinite(final_word_end)
            or not math.isfinite(duration_seconds)
            or first_word_start < 0
            or final_word_end < first_word_start
            or duration_seconds <= 0
            or final_word_end > duration_seconds + 0.25
        ):
            raise StateTransitionError(
                "Advisory terminal protection requires a verified speech window."
            )

        normalized_qa = dict(transcript_qa or {})
        normalized_qa.setdefault("automated_passed", False)
        normalized_qa["advisory_delivery_window_verified"] = True
        normalized_qa["first_word_start_seconds"] = first_word_start
        normalized_qa["final_word_end_seconds"] = min(
            duration_seconds,
            final_word_end,
        )
        take["transcript_qa"] = normalized_qa
        take["trim_window"] = {
            "start_seconds": 0.0,
            "end_seconds": min(duration_seconds, final_word_end + 0.25),
            "source": "deepgram_word_window",
        }
        return True

    @staticmethod
    def _delivery(run: Mapping[str, Any]) -> dict[str, Any]:
        artifact_manifest = run.get("artifact_manifest")
        artifacts = dict(artifact_manifest) if isinstance(artifact_manifest, Mapping) else {}
        delivery = artifacts.get("delivery")
        if not isinstance(delivery, Mapping) or delivery.get("passed") is not True:
            raise StateTransitionError("Semantic video delivery is not checksum verified.")
        raw = delivery.get("raw")
        captioned = delivery.get("captioned")
        if not isinstance(raw, Mapping) or not isinstance(captioned, Mapping):
            raise StateTransitionError("Semantic video delivery artifacts are incomplete.")
        for artifact in (raw, captioned):
            if (
                not str(artifact.get("url") or "").strip()
                or not re.fullmatch(r"[0-9a-f]{64}", str(artifact.get("sha256") or ""))
            ):
                raise StateTransitionError("Semantic video delivery artifact checksum is invalid.")
        return dict(delivery)

    def _project_delivery(self, run: Mapping[str, Any]) -> dict[str, Any]:
        delivery = self._delivery(run)
        raw = delivery["raw"]
        captioned = delivery["captioned"]
        return {
            "passed": True,
            "artifacts": dict(run.get("artifact_manifest") or {}),
            "final_video_uri": str(raw["url"]),
            "final_video_sha256": str(raw["sha256"]),
            "final_caption_uri": str(captioned["url"]),
            "final_caption_sha256": str(captioned["sha256"]),
        }


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _latest_attempts(takes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[int, dict[str, Any]] = {}
    for raw in takes:
        take = dict(raw)
        index = int(take["take_index"])
        if index not in latest or int(take.get("attempt") or 1) > int(latest[index].get("attempt") or 1):
            latest[index] = take
    return [latest[index] for index in sorted(latest)]


def _is_definitive_rejection(exc: Exception) -> bool:
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response is not None
        and exc.response.status_code in {400, 401, 403, 404, 422, 429}
    )


class SemanticVideoWorker:
    """Run one fenced stage or provider wave for one approved semantic video."""

    def __init__(
        self,
        *,
        repo: Optional[Any] = None,
        vertex: Optional[Any] = None,
        storage: Optional[Any] = None,
        stage_runner: Optional[Any] = None,
        video_loader: Callable[[str], bytes] = load_video_uri,
        worker_id: Optional[str] = None,
        max_inflight: int = DEFAULT_MAX_INFLIGHT,
        generation_gate: Optional[Any] = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        heartbeat_seconds: Optional[float] = None,
    ) -> None:
        if isinstance(max_inflight, bool) or max_inflight < 1 or max_inflight > 2:
            raise ValidationError("Semantic video max in-flight must be one or two.")
        if isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= 3600:
            raise ValidationError(
                "Semantic video lease duration must be between 1 and 3600 seconds."
            )
        resolved_heartbeat_seconds = (
            min(DEFAULT_HEARTBEAT_SECONDS, lease_seconds / 3)
            if heartbeat_seconds is None
            else heartbeat_seconds
        )
        if (
            isinstance(resolved_heartbeat_seconds, bool)
            or resolved_heartbeat_seconds <= 0
            or resolved_heartbeat_seconds >= lease_seconds
        ):
            raise ValidationError(
                "Semantic video heartbeat must be positive and shorter than the lease."
            )
        self.repo = repo or SemanticVideoRepository()
        self.vertex = vertex or VertexAIClient()
        self.storage = storage or get_storage_client()
        self.stage_runner = stage_runner or ProductionStageRunner(storage=self.storage)
        self.video_loader = video_loader
        self.worker_id = worker_id or f"semantic-video-contract-v2-{os.getpid()}"
        self.max_inflight = max_inflight
        self.generation_gate = generation_gate
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = float(resolved_heartbeat_seconds)

    def _release_run_best_effort(self, *, run_id: str, lease_token: str) -> None:
        try:
            self.repo.release_run(
                run_id=run_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "semantic_video_lease_release_failed",
                run_id=run_id,
                worker_id=self.worker_id,
                error=str(exc),
            )
            return
        logger.info(
            "semantic_video_lease_released",
            run_id=run_id,
            worker_id=self.worker_id,
        )

    def _reconcile_completed_batch(self, run: Mapping[str, Any]) -> None:
        reconcile = getattr(self.repo, "reconcile_batch_state", None)
        if not callable(reconcile):
            return
        batch_id = str(run.get("batch_id") or "").strip()
        if not batch_id:
            return
        reconcile(
            batch_id=batch_id,
            correlation_id=f"semantic_complete_{run.get('id')}",
        )

    def tick(self, run_id: Optional[str] = None) -> WorkerTickResult:
        run = self.repo.claim_run(
            run_id=run_id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if not run:
            return WorkerTickResult(run_id=run_id, stage=None, action="not_claimed")
        run = dict(run)
        claimed_id = str(run["id"])
        stage = str(run.get("stage") or "")
        lease_token = str(run.get("lease_token") or "")
        if stage not in EXECUTABLE_STAGES or not lease_token:
            if lease_token:
                self._release_run_best_effort(
                    run_id=claimed_id, lease_token=lease_token
                )
            return WorkerTickResult(run_id=claimed_id, stage=stage or None, action="not_claimed")

        logger.info(
            "semantic_video_lease_claimed",
            run_id=claimed_id,
            worker_id=self.worker_id,
            lease_expires_at=run.get("lease_expires_at"),
            lease_seconds=self.lease_seconds,
        )
        heartbeat = _LeaseHeartbeat(
            repo=self.repo,
            run_id=claimed_id,
            worker_id=self.worker_id,
            lease_token=lease_token,
            lease_seconds=self.lease_seconds,
            interval_seconds=self.heartbeat_seconds,
        )
        heartbeat.start()
        try:
            takes = _latest_attempts(self.repo.list_attempts(claimed_id))
            if stage == "generating":
                if self.generation_gate is None:
                    return self._run_generation_wave(run, takes, lease_token)
                if not self.generation_gate.acquire(blocking=False):
                    return WorkerTickResult(
                        claimed_id,
                        stage,
                        "generation_capacity_wait",
                    )
                try:
                    return self._run_generation_wave(run, takes, lease_token)
                finally:
                    self.generation_gate.release()
            return self._run_post_generation_stage(run, takes, lease_token)
        except Exception as exc:
            error = {
                "code": type(exc).__name__,
                "message": str(exc)[:500],
                "worker_id": self.worker_id,
            }
            try:
                self.repo.persist_worker_exception(
                    run_id=claimed_id,
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                    stage=stage,
                    error=error,
                )
            except Exception as persistence_exc:  # noqa: BLE001
                logger.exception(
                    "semantic_video_worker_exception_persistence_failed",
                    run_id=claimed_id,
                    stage=stage,
                    original_error=str(exc),
                    persistence_error=str(persistence_exc),
                )
            raise
        finally:
            heartbeat.stop()
            self._release_run_best_effort(run_id=claimed_id, lease_token=lease_token)

    def _run_generation_wave(
        self,
        run: Mapping[str, Any],
        takes: list[dict[str, Any]],
        lease_token: str,
    ) -> WorkerTickResult:
        run_id = str(run["id"])
        states = {str(take.get("submission_state") or "") for take in takes}
        if "submission_unknown" in states or "intent_persisted" in states:
            return WorkerTickResult(run_id, "generating", "blocked_unknown_submission")

        submitted = [take for take in takes if take.get("submission_state") == "submitted"]
        if submitted:
            processed = self._poll_wave(run, submitted[: self.max_inflight], lease_token)
            if processed < 0:
                return WorkerTickResult(
                    run_id, "retry_approval_required", "provider_failed"
                )
            return WorkerTickResult(run_id, "generating", "raw_completed" if processed else "polling", processed)

        if takes and all(take.get("submission_state") == "completed" for take in takes):
            self.repo.advance_stage(
                run_id=run_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                expected_stage="generating",
                next_stage="transcript_qa",
                artifacts={},
            )
            return WorkerTickResult(run_id, "transcript_qa", "stage_advanced")

        pending = [
            take
            for take in takes
            if take.get("submission_state") in {"planned", "reserved"}
        ]
        if not pending:
            return WorkerTickResult(run_id, "generating", "waiting")

        master_bytes, shot_bytes_by_index = self._verified_shot_bytes(run, takes)
        del master_bytes
        submitted_count = 0
        for take in pending[: self.max_inflight]:
            request_contract = take.get("request_contract")
            if not isinstance(request_contract, Mapping):
                raise ValidationError("Semantic video take request contract is missing.")
            reserved = (
                self.repo.reserve_submission(
                    run_id=run_id,
                    take_id=str(take["id"]),
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                )
                if take.get("submission_state") == "planned"
                else take
            )
            request_hash = str(reserved.get("request_hash") or "")
            self.repo.persist_submission_intent(
                run_id=run_id,
                take_id=str(take["id"]),
                worker_id=self.worker_id,
                lease_token=lease_token,
                request_hash=request_hash,
            )
            correlation_id = (
                f"semantic_ugc_{run_id}_take_{int(take['take_index'])}"
                f"_attempt_{int(take.get('attempt') or 1)}"
            )
            try:
                result = self.vertex.submit_image_video(
                    prompt=str(request_contract.get("prompt") or ""),
                    image_bytes=shot_bytes_by_index[int(take["take_index"])],
                    mime_type=str((take.get("shot_transform") or {}).get("mime_type") or "image/png"),
                    correlation_id=correlation_id,
                    aspect_ratio=str(request_contract.get("aspect_ratio") or "9:16"),
                    duration_seconds=int(request_contract.get("provider_duration_seconds") or 0),
                    model=str(request_contract.get("provider_model") or take.get("provider_model") or ""),
                    negative_prompt=str(request_contract.get("negative_prompt") or ""),
                    seed=int(request_contract["seed"]) if request_contract.get("seed") is not None else None,
                    sample_count=1,
                    generate_audio=True,
                    resolution=str(request_contract.get("resolution") or run.get("resolution") or "720p"),
                )
                operation_id = str(result.get("operation_id") or "").strip()
                if not operation_id:
                    raise ValidationError("Vertex response is missing an operation id.")
            except Exception as exc:
                error = {
                    "code": "provider_rejected" if _is_definitive_rejection(exc) else "submission_unknown",
                    "message": str(exc)[:500],
                    "correlation_id": correlation_id,
                }
                if _is_definitive_rejection(exc):
                    self.repo.persist_provider_failure(
                        run_id=run_id,
                        take_id=str(take["id"]),
                        worker_id=self.worker_id,
                        lease_token=lease_token,
                        error=error,
                    )
                    return WorkerTickResult(run_id, "retry_approval_required", "provider_failed", submitted_count)
                self.repo.persist_submission_unknown(
                    run_id=run_id,
                    take_id=str(take["id"]),
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                    error=error,
                )
                return WorkerTickResult(run_id, "generating", "submission_unknown", submitted_count)

            self.repo.persist_accepted_operation(
                run_id=run_id,
                take_id=str(take["id"]),
                worker_id=self.worker_id,
                lease_token=lease_token,
                operation_id=operation_id,
                provider_model=str(request_contract.get("provider_model") or take.get("provider_model") or ""),
            )
            submitted_count += 1
        return WorkerTickResult(run_id, "generating", "submitted", submitted_count)

    def _verified_shot_bytes(
        self,
        run: Mapping[str, Any],
        takes: Sequence[Mapping[str, Any]],
    ) -> tuple[bytes, dict[int, bytes]]:
        master = run.get("master_snapshot")
        if not isinstance(master, Mapping):
            raise ValidationError("Semantic video approved master snapshot is missing.")
        reference = run.get("reference_snapshot")
        if not isinstance(reference, Mapping):
            raise ValidationError("Semantic video immutable reference snapshot is missing.")
        actor_references = reference.get("actor_references")
        if not isinstance(actor_references, list) or len(actor_references) != 2:
            raise ValidationError(
                "Semantic video requires two immutable actor references before paid submission."
            )
        expected_roles = ("actor_front", "actor_three_quarter")
        verified_actor_rows: list[dict[str, Any]] = []
        for index, (row, expected_role) in enumerate(
            zip(actor_references, expected_roles)
        ):
            if not isinstance(row, Mapping) or str(row.get("role") or "") != expected_role:
                raise StateTransitionError(
                    "Semantic video actor reference order changed before paid submission.",
                    {"index": index, "expected_role": expected_role},
                )
            reference_bytes = self.storage.download_video(
                video_url=str(row.get("storage_uri") or ""),
                correlation_id=f"semantic_ugc_{run['id']}_{expected_role}",
            )
            if (
                sha256(reference_bytes).hexdigest()
                != str(row.get("sha256") or "").lower()
                or len(reference_bytes) != int(row.get("byte_length") or -1)
                or not str(row.get("mime_type") or "").lower().startswith("image/")
            ):
                raise StateTransitionError(
                    "Semantic video actor reference changed before paid submission.",
                    {"role": expected_role},
                )
            verified_actor_rows.append(dict(row))
        actor_fingerprint = build_actor_reference_fingerprint(verified_actor_rows)
        if (
            actor_fingerprint
            != str(reference.get("actor_reference_fingerprint") or "").lower()
        ):
            raise StateTransitionError(
                "Semantic video actor-reference fingerprint changed before paid submission."
            )
        generation_contract = validate_scene_plate_generation_contract(
            reference.get("scene_plate_generation_contract"),
            actor_reference_fingerprint=actor_fingerprint,
        )
        visual_contract = validate_visual_contract(reference.get("visual_contract"))
        if (
            str(master.get("generation_contract_hash") or "").lower()
            != generation_contract["contract_hash"]
            or str(master.get("visual_contract_hash") or "").lower()
            != visual_contract["contract_hash"]
            or str(master.get("actor_reference_fingerprint") or "").lower()
            != actor_fingerprint
            or str(master.get("provider_model") or "")
            != generation_contract["model"]
        ):
            raise StateTransitionError(
                "Semantic video approved master contract changed before paid submission."
            )
        validate_approved_scene_plate_identity(
            master,
            actor_reference_fingerprint=actor_fingerprint,
            generation_contract=generation_contract,
        )
        plan = run.get("plan_snapshot")
        if (
            not isinstance(plan, Mapping)
            or str(plan.get("generation_contract_hash") or "").lower()
            != generation_contract["contract_hash"]
            or str(plan.get("actor_reference_fingerprint") or "").lower()
            != actor_fingerprint
            or str(plan.get("visual_contract_hash") or "").lower()
            != visual_contract["contract_hash"]
        ):
            raise StateTransitionError(
                "Semantic video paid plan no longer matches its identity contracts."
            )
        master_uri = str(master.get("storage_uri") or "")
        master_bytes = self.storage.download_video(
            video_url=master_uri,
            correlation_id=f"semantic_ugc_{run['id']}_master",
        )
        expected_master_hash = str(run.get("master_hash") or master.get("sha256") or "")
        if (
            sha256(master_bytes).hexdigest() != expected_master_hash
            or int(master.get("byte_length") or -1) != len(master_bytes)
        ):
            raise StateTransitionError("Semantic video approved master changed before paid submission.")
        shot_count = max(int(take["take_index"]) for take in takes) + 1
        deck = derive_shot_deck(
            approved_master_bytes=master_bytes,
            expected_sha256=expected_master_hash,
            mime_type=str(master.get("mime_type") or "image/png"),
            shot_count=shot_count,
        )
        result = {shot.index: shot.image_bytes for shot in deck}
        for take in takes:
            index = int(take["take_index"])
            transform = take.get("shot_transform")
            contract = take.get("request_contract")
            if not isinstance(transform, Mapping) or not isinstance(contract, Mapping):
                raise ValidationError("Semantic video shot contract is missing.")
            expected_shot_hash = str(transform.get("output_sha256") or "")
            if (
                sha256(result[index]).hexdigest() != expected_shot_hash
                or str(contract.get("shot_sha256") or "") != expected_shot_hash
            ):
                raise StateTransitionError("Semantic video shot contract changed before paid submission.")
        return master_bytes, result

    def _poll_wave(
        self,
        run: Mapping[str, Any],
        submitted: Sequence[Mapping[str, Any]],
        lease_token: str,
    ) -> int:
        run_id = str(run["id"])
        completed = 0
        for take in submitted:
            operation_id = str(take.get("operation_id") or "")
            if not operation_id:
                raise StateTransitionError("Accepted semantic video operation has no operation id.")
            result = self.vertex.check_operation_status(
                operation_id=operation_id,
                correlation_id=f"semantic_ugc_{run_id}_poll_{take['id']}",
            )
            if not result.get("done"):
                continue
            if result.get("status") != "completed" or not str(result.get("video_uri") or "").strip():
                self.repo.persist_provider_failure(
                    run_id=run_id,
                    take_id=str(take["id"]),
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                    error={"code": "provider_operation_failed", "details": result.get("error")},
                )
                return -1
            provider_uri = str(result["video_uri"])
            video_bytes = self.video_loader(provider_uri)
            digest = sha256(video_bytes).hexdigest()
            object_key = (
                f"{str(run.get('artifact_prefix') or '').strip('/')}/raw/"
                f"take-{int(take['take_index'])}-attempt-{int(take.get('attempt') or 1)}/{digest}.mp4"
            )
            upload = self.storage.upload_video(
                video_bytes=video_bytes,
                file_name=f"{digest}.mp4",
                correlation_id=f"semantic_ugc_{run_id}_raw_{take['id']}",
                object_key=object_key,
            )
            if (
                str(upload.get("storage_key") or "") != object_key
                or str(upload.get("sha256") or "") != digest
                or int(upload.get("size") or -1) != len(video_bytes)
            ):
                raise StateTransitionError("Semantic video raw artifact receipt is invalid.")
            self.repo.persist_completed_take(
                run_id=run_id,
                take_id=str(take["id"]),
                worker_id=self.worker_id,
                lease_token=lease_token,
                provider_video_uri=(
                    f"vertex-operation://{operation_id}"
                    if provider_uri.startswith("data:")
                    else provider_uri
                ),
                raw_artifact_uri=str(upload["url"]),
                raw_artifact_sha256=digest,
            )
            completed += 1
        return completed

    def _run_post_generation_stage(
        self,
        run: Mapping[str, Any],
        takes: list[dict[str, Any]],
        lease_token: str,
    ) -> WorkerTickResult:
        run_id = str(run["id"])
        stage = str(run["stage"])
        try:
            artifact_manifest = run.get("artifact_manifest")
            qa_advisory = (
                artifact_manifest.get("qa_advisory")
                if isinstance(artifact_manifest, Mapping)
                else None
            )
            if (
                stage == "transcript_qa"
                and isinstance(qa_advisory, Mapping)
                and qa_advisory.get("required") is True
                and qa_advisory.get("stage") == "transcript_qa"
                and not run.get("failure_envelope")
            ):
                result = self.stage_runner.accept_transcript_advisory(
                    run=dict(run),
                    takes=deepcopy_rows(takes),
                )
            else:
                result = self.stage_runner.run_stage(
                    stage=stage,
                    run=dict(run),
                    takes=deepcopy_rows(takes),
                )
        except ThirdPartyError as exc:
            if stage != "identity_qa":
                raise
            failed_indexes = sorted(
                {int(take["take_index"]) for take in takes}
            )
            if not failed_indexes:
                raise StateTransitionError(
                    "Identity QA service failure requires durable take indexes."
                ) from exc
            result = {
                "passed": False,
                "failed_take_indexes": failed_indexes,
                "artifacts": {
                    "qa_failure": {
                        "stage": stage,
                        "message": exc.message,
                        "details": exc.details,
                        "failed_take_indexes": failed_indexes,
                        "failure_type": "qa_service_unavailable",
                        "retry_mode": "qa_only",
                    },
                    "guidance": (
                        "Retry identity QA using the existing checksum-addressed "
                        "takes. Do not submit new paid Veo work."
                    ),
                },
            }
        if not isinstance(result, Mapping):
            raise StateTransitionError("Semantic video stage runner returned an invalid contract.")
        artifacts = dict(result.get("artifacts") or {})
        if not result.get("passed"):
            if self._is_single_paid_eight_second_delivery(run, takes):
                return self._complete_advisory_delivery(
                    run=run,
                    takes=takes,
                    lease_token=lease_token,
                    failed_stage=stage,
                    artifacts=artifacts,
                )
            # Transcript QA is a hard dependency for every later production
            # stage: contact sheets, voice QA, and composition all consume its
            # verified speech window. Advancing a failed transcript as an
            # advisory creates an impossible run that workers reclaim forever.
            # Identity and voice evaluator failures remain advisory because
            # their downstream stages can still produce a reviewable delivery.
            if stage in {"identity_qa", "voice_qa"}:
                failed_indexes = sorted(
                    {int(index) for index in result.get("failed_take_indexes") or []}
                )
                qa_failure = artifacts.get("qa_failure")
                advisory = {
                    "required": True,
                    "stage": stage,
                    "failed_take_indexes": failed_indexes,
                    "message": (
                        str(qa_failure.get("message") or "")
                        if isinstance(qa_failure, Mapping)
                        else "Automated QA recommends manual review."
                    ),
                    "paid_retry_required": False,
                }
                next_stage = NEXT_STAGE[stage]
                self.repo.advance_stage(
                    run_id=run_id,
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                    expected_stage=stage,
                    next_stage=next_stage,
                    artifacts={
                        **artifacts,
                        "qa_advisory": advisory,
                        "qa_failure": None,
                    },
                )
                logger.warning(
                    "semantic_video_qa_delivered_as_advisory",
                    run_id=run_id,
                    failed_stage=stage,
                    failed_take_indexes=failed_indexes,
                )
                return WorkerTickResult(
                    run_id,
                    next_stage,
                    "stage_advanced_with_qa_advisory",
                )
            failed_indexes = sorted({int(index) for index in result.get("failed_take_indexes") or []})
            if not failed_indexes:
                raise StateTransitionError("Failed semantic video QA requires failed take indexes.")
            self.repo.require_retry_approval(
                run_id=run_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                expected_stage=stage,
                failed_take_indexes=failed_indexes,
                evidence=artifacts,
            )
            return WorkerTickResult(run_id, "retry_approval_required", "retry_approval_required")
        if stage == "uploading":
            self.repo.complete_run(
                run_id=run_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                final_video_uri=str(result.get("final_video_uri") or ""),
                final_video_sha256=str(result.get("final_video_sha256") or ""),
                final_caption_uri=str(result.get("final_caption_uri") or ""),
                final_caption_sha256=str(result.get("final_caption_sha256") or ""),
                artifact_manifest=artifacts,
            )
            self._reconcile_completed_batch(run)
            cleanup = getattr(self.stage_runner, "cleanup_run_workspace", None)
            if callable(cleanup):
                cleanup(run_id)
            return WorkerTickResult(run_id, "completed", "completed")
        next_stage = NEXT_STAGE[stage]
        self.repo.advance_stage(
            run_id=run_id,
            worker_id=self.worker_id,
            lease_token=lease_token,
            expected_stage=stage,
            next_stage=next_stage,
            artifacts=artifacts,
        )
        return WorkerTickResult(run_id, next_stage, "stage_advanced")

    @staticmethod
    def _is_single_paid_eight_second_delivery(
        run: Mapping[str, Any],
        takes: Sequence[Mapping[str, Any]],
    ) -> bool:
        if int(run.get("requested_duration_seconds") or 0) != 8 or len(takes) != 1:
            return False
        take = takes[0]
        return (
            int(take.get("provider_duration_seconds") or 0) == 8
            and str(take.get("submission_state") or "") == "completed"
            and bool(str(take.get("raw_artifact_uri") or "").strip())
            and bool(re.fullmatch(r"[0-9a-f]{64}", str(take.get("raw_artifact_sha256") or "")))
        )

    def _complete_advisory_delivery(
        self,
        *,
        run: Mapping[str, Any],
        takes: Sequence[Mapping[str, Any]],
        lease_token: str,
        failed_stage: str,
        artifacts: Mapping[str, Any],
    ) -> WorkerTickResult:
        """Deliver one paid 8s take even when evaluator QA recommends manual review."""
        run_id = str(run["id"])
        take = takes[0]
        raw_uri = str(take["raw_artifact_uri"])
        raw_hash = str(take["raw_artifact_sha256"])
        delivery_run = dict(run)
        delivery_run["artifact_manifest"] = {
            **dict(run.get("artifact_manifest") or {}),
            **dict(artifacts),
        }
        try:
            captioned = self.stage_runner.caption_advisory_single_take(
                run=delivery_run,
                takes=deepcopy_rows(takes),
            )
        except StateTransitionError as exc:
            retryable_terminal_failures = {
                "Advisory terminal protection would cut transcript-safe context.": (
                    "terminal_tail_speech_overlap",
                    "Retry only the take whose verified final word overlaps the protected "
                    "terminal window. Preserve every completed sibling take.",
                    "terminal_speech_overlap_retry_required",
                ),
                "Advisory active-speech cut exceeds the cadence bound.": (
                    "terminal_active_speech_timing",
                    "Retry only the take whose final word ends too early for an active-speech "
                    "cut within the cadence bound. Preserve every completed sibling take.",
                    "terminal_active_speech_retry_required",
                ),
            }
            retry_policy = retryable_terminal_failures.get(str(exc))
            if retry_policy is None:
                raise
            failure_type, retry_guidance, retry_action = retry_policy
            terminal_failure = {
                "stage": "acoustic_qa",
                "message": str(exc),
                "details": dict(exc.details or {}),
                "failed_take_indexes": [int(take.get("take_index") or 0)],
                "failure_type": failure_type,
                "retry_mode": "localized_paid_take",
            }
            self.repo.require_retry_approval(
                run_id=run_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                expected_stage=failed_stage,
                failed_take_indexes=terminal_failure["failed_take_indexes"],
                evidence={
                    **dict(artifacts),
                    "qa_failure": terminal_failure,
                    "guidance": retry_guidance,
                },
            )
            return WorkerTickResult(
                run_id,
                "retry_approval_required",
                retry_action,
            )
        caption_uri = str(captioned.get("url") or "")
        caption_hash = str(captioned.get("sha256") or "")
        if (
            not caption_uri
            or not re.fullmatch(r"[0-9a-f]{64}", caption_hash)
            or caption_hash == raw_hash
        ):
            raise StateTransitionError(
                "Advisory delivery requires a distinct captioned artifact."
            )
        qa_failure = artifacts.get("qa_failure")
        advisory = {
            "required": True,
            "stage": failed_stage,
            "message": (
                str(qa_failure.get("message") or "")
                if isinstance(qa_failure, Mapping)
                else "Automated QA recommends manual review."
            ),
            "paid_retry_required": False,
        }
        delivery = {
            "passed": True,
            "mode": "single_paid_take_manual_review",
            "raw": {"url": raw_uri, "sha256": raw_hash},
            "captioned": {"url": caption_uri, "sha256": caption_hash},
            "qa_advisory": advisory,
        }
        completion_artifacts = {
            **dict(artifacts),
            "pipeline_manifest": captioned.get("pipeline_manifest"),
            "delivery": delivery,
            "qa_advisory": advisory,
            "qa_failure": None,
        }

        current_stage = failed_stage
        while current_stage != "uploading":
            next_stage = NEXT_STAGE[current_stage]
            self.repo.advance_stage(
                run_id=run_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                expected_stage=current_stage,
                next_stage=next_stage,
                artifacts=completion_artifacts if current_stage == failed_stage else {},
            )
            current_stage = next_stage
        self.repo.complete_run(
            run_id=run_id,
            worker_id=self.worker_id,
            lease_token=lease_token,
            final_video_uri=raw_uri,
            final_video_sha256=raw_hash,
            final_caption_uri=caption_uri,
            final_caption_sha256=caption_hash,
            artifact_manifest=completion_artifacts,
        )
        self._reconcile_completed_batch(run)
        cleanup = getattr(self.stage_runner, "cleanup_run_workspace", None)
        if callable(cleanup):
            cleanup(run_id)
        logger.warning(
            "semantic_video_single_take_delivered_with_qa_advisory",
            run_id=run_id,
            failed_stage=failed_stage,
            raw_sha256=raw_hash[:12],
        )
        return WorkerTickResult(run_id, "completed", "completed_with_qa_advisory")


def deepcopy_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return json.loads(json.dumps([dict(row) for row in rows], default=str))


def _worker_concurrency() -> int:
    raw = os.getenv(
        "SEMANTIC_VIDEO_WORKER_CONCURRENCY",
        str(DEFAULT_WORKER_CONCURRENCY),
    )
    try:
        concurrency = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Semantic video worker concurrency must be an integer."
        ) from exc
    if not 1 <= concurrency <= MAX_WORKER_CONCURRENCY:
        raise ValidationError(
            f"Semantic video worker concurrency must be between 1 and {MAX_WORKER_CONCURRENCY}."
        )
    return concurrency


def _run_worker_loop(
    worker: SemanticVideoWorker,
    *,
    poll_seconds: float,
    stop_event: threading.Event,
) -> None:
    logger.info("semantic_video_worker_slot_started", worker_id=worker.worker_id)
    while not stop_event.is_set():
        try:
            worker.tick()
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "semantic_video_worker_tick_failed",
                worker_id=worker.worker_id,
                error=str(exc),
            )
        stop_event.wait(poll_seconds)


def main() -> None:
    poll_seconds = max(1.0, float(os.getenv("SEMANTIC_VIDEO_WORKER_POLL_SECONDS", "5")))
    concurrency = _worker_concurrency()
    generation_gate = threading.BoundedSemaphore(1)
    work_root = Path(
        os.getenv("SEMANTIC_VIDEO_WORK_ROOT", "/tmp/semantic-video-worker")
    )
    stop_event = threading.Event()
    threads: list[threading.Thread] = []
    for slot in range(concurrency):
        worker_id = f"semantic-video-contract-v2-{os.getpid()}-{slot + 1}"
        worker = SemanticVideoWorker(
            worker_id=worker_id,
            generation_gate=generation_gate,
            stage_runner=ProductionStageRunner(
                work_root=work_root / f"slot-{slot + 1}"
            ),
        )
        threads.append(
            threading.Thread(
                target=_run_worker_loop,
                kwargs={
                    "worker": worker,
                    "poll_seconds": poll_seconds,
                    "stop_event": stop_event,
                },
                name=f"semantic-video-slot-{slot + 1}",
            )
        )

    logger.info(
        "semantic_video_worker_started",
        concurrency=concurrency,
        generation_wave_concurrency=1,
    )
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(timeout=1.0)
    except KeyboardInterrupt:
        logger.info("semantic_video_worker_stopping")
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=max(10.0, DEFAULT_LEASE_SECONDS / 4))


if __name__ == "__main__":
    main()
