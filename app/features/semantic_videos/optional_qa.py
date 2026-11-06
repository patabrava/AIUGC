"""Explicit deferred gates and public post-delivery review state."""
from collections.abc import Mapping
from typing import Any


def delivery_gate_satisfied(manifest: Mapping[str, Any], key: str) -> bool:
    report = manifest.get(key)
    if not isinstance(report, Mapping):
        return False
    return report.get('passed') is True or (
        manifest.get('optional_qa_deferred') is True
        and report.get('status') == 'deferred'
        and key in {'visual_qa', 'voice_qa'}
    )


def optional_qa_projection(run: Mapping[str, Any]) -> dict[str, Any] | None:
    artifacts = run.get('artifact_manifest') or {}
    review = artifacts.get('optional_qa') or {}
    status = review.get('status')
    if status not in {'pending', 'running', 'completed', 'failed'}:
        return None
    # Do not expose leases, local paths, transport failures or provider payloads.
    findings = [str(value)[:1000] for value in review.get('findings', []) if isinstance(value, str)]
    return {'status': status, 'findings': findings[:20]}
