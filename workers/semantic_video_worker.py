"""Lease-fenced Semantic UGC worker with fail-closed paid submissions.

The worker performs one provider wave or one post-generation stage per tick. All
paid state transitions are delegated to transaction-safe repository RPCs so a
crash can never be guessed safe to resubmit.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping, Optional, Sequence

import httpx

from app.adapters.storage_client import get_storage_client
from app.adapters.vertex_ai_client import VertexAIClient
from app.core.errors import StateTransitionError, ValidationError
from app.core.logging import get_logger
from app.features.semantic_videos import queries
from app.features.shot_production.runner import load_video_uri
from app.features.shot_production.shot_deck import derive_shot_deck


logger = get_logger(__name__)
DEFAULT_MAX_INFLIGHT = 2
DEFAULT_LEASE_SECONDS = 300
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

    def release_run(self, **kwargs):
        return queries.release_worker_lease(**kwargs)


class ProductionStageRunner:
    """Bridge persisted runs into the existing audited shot-production pipeline."""

    _MANIFEST_GLOBAL_KEYS = (
        "contact_sheet",
        "visual_qa",
        "voice_qa",
        "stitch",
        "final_transcript",
        "final_transcript_qa",
        "seam_qa",
        "acoustic_seam_plan",
        "acoustic_seam_qa",
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
    ) -> None:
        self.storage = storage or get_storage_client()
        self.deepgram = deepgram
        self.work_root = Path(
            work_root or os.getenv("SEMANTIC_VIDEO_WORK_ROOT", "/tmp/semantic-video-worker")
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
        try:
            if stage == "transcript_qa":
                pipeline = self._runner()
                pipeline.transcribe_and_validate_takes(manifest_path, self._deepgram())
            elif stage == "identity_qa":
                pipeline = self._runner()
                pipeline.build_contact_sheet(manifest_path)
                pipeline.run_visual_qa(manifest_path)
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

    @staticmethod
    def _runner():
        from app.features.shot_production import runner

        return runner

    def _deepgram(self):
        if self.deepgram is None:
            from app.adapters.deepgram_client import get_deepgram_client

            self.deepgram = get_deepgram_client()
        return self.deepgram

    def _materialize_manifest(
        self,
        run: Mapping[str, Any],
        takes: Sequence[Mapping[str, Any]],
    ) -> Path:
        pipeline = self._runner()
        run_id = str(run["id"])
        run_dir = self.work_root / run_id
        raw_dir = run_dir / "raw"
        run_dir.mkdir(parents=True, exist_ok=True)
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

        artifact_manifest = run.get("artifact_manifest")
        artifacts = dict(artifact_manifest) if isinstance(artifact_manifest, Mapping) else {}
        prior = artifacts.get("pipeline_manifest")
        prior_manifest = dict(prior) if isinstance(prior, Mapping) else {}
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
                "transcript_qa": previous.get("transcript_qa"),
                "trim_window": previous.get("trim_window"),
            }
            manifest_takes.append(row)

        requested_duration = int(run.get("requested_duration_seconds") or 0)
        script_snapshot = run.get("script_snapshot")
        script = dict(script_snapshot) if isinstance(script_snapshot, Mapping) else {}
        script_text = str(script.get("text") or "")
        delivery_contract = {
            "requested": float(requested_duration),
            "minimum": max(0.5, float(requested_duration) - 1.5),
            "maximum": float(requested_duration) + 0.5,
        }
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
            "script": {
                "path": "",
                "input_sha256": str(run.get("script_hash") or ""),
                "text_sha256": sha256(script_text.encode("utf-8")).hexdigest(),
                "source": pipeline.APP_SCRIPT_SOURCE,
                "category": "semantic_ugc",
                "target_length_tier": requested_duration,
                "planning_profile": pipeline.PLANNING_PROFILE,
                "delivery_duration_seconds": delivery_contract,
                "text": script_text,
                "planned_provider_durations": [take["duration_seconds"] for take in manifest_takes],
                "source_payload": {"source": pipeline.APP_SCRIPT_SOURCE},
            },
            "takes": manifest_takes,
        }
        for key in self._MANIFEST_GLOBAL_KEYS:
            if key in prior_manifest:
                payload[key] = prior_manifest[key]
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
                for index in (payload.get("acoustic_seam_qa") or {}).get(
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
        return {
            "passed": False,
            "failed_take_indexes": failed,
            "artifacts": {
                "pipeline_manifest": payload,
                "qa_failure": {
                    "stage": stage,
                    "message": exc.message,
                    "details": exc.details,
                    "failed_take_indexes": failed,
                },
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
        pipeline.compose_and_caption(
            manifest_path,
            self._deepgram(),
            acoustic_seams=len(takes) > 1,
        )
        payload = self._read_manifest(manifest_path)
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
            "raw": {"url": str(raw_upload["url"]), "sha256": raw_hash},
            "captioned": {"url": str(caption_upload["url"]), "sha256": caption_hash},
            "acoustic_status": (
                "evaluated" if len(takes) > 1 else "not_applicable"
            ),
        }
        return {
            "passed": True,
            "artifacts": {
                "pipeline_manifest": payload,
                "acoustic_qa": payload.get("acoustic_seam_qa")
                or {"passed": True, "status": "not_applicable"},
                "delivery": delivery,
            },
        }

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
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        if isinstance(max_inflight, bool) or max_inflight < 1 or max_inflight > 2:
            raise ValidationError("Semantic video max in-flight must be one or two.")
        if isinstance(lease_seconds, bool) or lease_seconds < 1:
            raise ValidationError("Semantic video lease duration must be positive.")
        self.repo = repo or SemanticVideoRepository()
        self.vertex = vertex or VertexAIClient()
        self.storage = storage or get_storage_client()
        self.stage_runner = stage_runner or ProductionStageRunner(storage=self.storage)
        self.video_loader = video_loader
        self.worker_id = worker_id or f"semantic-video-{os.getpid()}"
        self.max_inflight = max_inflight
        self.lease_seconds = lease_seconds

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
                self.repo.release_run(
                    run_id=claimed_id,
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                )
            return WorkerTickResult(run_id=claimed_id, stage=stage or None, action="not_claimed")

        try:
            takes = _latest_attempts(self.repo.list_attempts(claimed_id))
            if stage == "generating":
                return self._run_generation_wave(run, takes, lease_token)
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
            self.repo.release_run(
                run_id=claimed_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
            )

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
        result = self.stage_runner.run_stage(stage=stage, run=dict(run), takes=deepcopy_rows(takes))
        if not isinstance(result, Mapping):
            raise StateTransitionError("Semantic video stage runner returned an invalid contract.")
        artifacts = dict(result.get("artifacts") or {})
        if not result.get("passed"):
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


def deepcopy_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return json.loads(json.dumps([dict(row) for row in rows], default=str))


def main() -> None:
    poll_seconds = max(1.0, float(os.getenv("SEMANTIC_VIDEO_WORKER_POLL_SECONDS", "5")))
    worker = SemanticVideoWorker()
    logger.info("semantic_video_worker_started", worker_id=worker.worker_id)
    while True:
        try:
            worker.tick()
        except Exception as exc:  # noqa: BLE001
            logger.exception("semantic_video_worker_tick_failed", error=str(exc))
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
