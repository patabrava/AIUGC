from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import io
import json
import re
import sys

from PIL import Image


APPROVED_50_SECOND_SCRIPT = " ".join(
    (
        "Wenn jede Treppe plötzlich zum Hindernis wird, verliert dein Alltag schnell an Freiheit, Ruhe und Spontaneität.",
        "Mit einem passenden Treppenlift bewegst du dich zuhause wieder sicher, selbstständig und ohne tägliche Umwege weiter.",
        "Unsere Beratung betrachtet deine Wohnsituation genau und erklärt verständlich, welche Lösung wirklich zu deinem Leben passt.",
        "Dabei bleiben wichtige Details wie Platzbedarf, Bedienung, Komfort und Finanzierung von Anfang an transparent für dich.",
        "Du erhältst keine pauschale Empfehlung, sondern eine ehrliche Einschätzung, die deine persönlichen Prioritäten konsequent vollständig berücksichtigt.",
        "So wird aus einer belastenden Barriere wieder ein Zuhause, in dem du dich selbstverständlich bewegen kannst.",
        "Vereinbare jetzt dein kostenloses Gespräch und finde gemeinsam mit unserem Team den nächsten Schritt für dich.",
    )
)


def _png_bytes(*, accent: int = 0) -> bytes:
    image = Image.new("RGB", (90, 160))
    image.putdata(
        [
            (
                (x * 255 // 89 + accent) % 256,
                (y * 255 // 159 + accent) % 256,
                (x + y + accent) % 256,
            )
            for y in range(160)
            for x in range(90)
        ]
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _snapshots(*, script: str = APPROVED_50_SECOND_SCRIPT, duration: int = 50, master: bytes):
    from app.features.semantic_videos.visual_contract import (
        SCENE_IDENTITY_COMPONENT_FIELDS,
        build_actor_reference_fingerprint,
        build_scene_plate_generation_contract,
        build_visual_contract,
    )

    master_hash = sha256(master).hexdigest()
    post = {
        "id": "00000000-0000-0000-0000-000000000101",
        "batch_id": "00000000-0000-0000-0000-000000000201",
        "script_review_status": "approved",
        "script": script,
    }
    batch = {
        "id": post["batch_id"],
        "creation_mode": "semantic_ugc",
        "target_duration_seconds": duration,
    }
    reference = {
        "actor_identity_id": "00000000-0000-0000-0000-000000000301",
        "actor": {"name": "AYRA Actor", "character_description": "Immutable actor description."},
        "actor_references": [
            {
                "role": "actor_front",
                "storage_uri": "semantic/references/front.png",
                "mime_type": "image/png",
                "byte_length": 101,
                "sha256": "1" * 64,
            },
            {
                "role": "actor_three_quarter",
                "storage_uri": "semantic/references/three-quarter.png",
                "mime_type": "image/png",
                "byte_length": 102,
                "sha256": "2" * 64,
            },
        ],
        "location_reference": {
            "role": "location",
            "storage_uri": "semantic/references/location.png",
            "mime_type": "image/png",
            "byte_length": 123,
            "sha256": "3" * 64,
        },
        "scene_key": "garden_patio_a",
        "scene_description": "the exact supplied garden patio",
        "wardrobe_key": "grey_cardigan",
        "wardrobe_description": "light-grey cardigan over a plain white top",
    }
    visual_contract = build_visual_contract(reference)
    reference["visual_contract"] = visual_contract
    actor_fingerprint = build_actor_reference_fingerprint(reference["actor_references"])
    reference["actor_reference_fingerprint"] = actor_fingerprint
    generation_contract = build_scene_plate_generation_contract(
        actor_reference_fingerprint=actor_fingerprint
    )
    reference["scene_plate_generation_contract"] = generation_contract
    reference["master"] = {
            "storage_uri": "semantic/masters/approved.png",
            "mime_type": "image/png",
            "byte_length": len(master),
            "sha256": master_hash,
            "provider_model": generation_contract["model"],
            "visual_contract_hash": visual_contract["contract_hash"],
            "actor_reference_fingerprint": actor_fingerprint,
            "generation_contract_hash": generation_contract["contract_hash"],
            "identity_gate_result": {
                "status": "passed",
                "passed": True,
                "evaluator_model": generation_contract["identity_evaluator_model"],
                "evaluator_contract_version": generation_contract[
                    "identity_evaluator_contract_version"
                ],
                "evaluated_actor_reference_fingerprint": actor_fingerprint,
                "candidate_sha256": master_hash,
                "component_results": {
                    field: True for field in SCENE_IDENTITY_COMPONENT_FIELDS
                },
                "confidence": 0.99,
                "blocking_reasons": [],
                "observed_differences": [],
                "evaluated_at": "2026-07-26T00:00:00+00:00",
            },
            "identity_attestation": True,
            "attestation_version": "semantic-actor-identity-v1",
            "approved_by": "operator@example.com",
            "approved_at": "2026-07-26T00:00:00+00:00",
    }
    return post, batch, reference


def _compile(*, script: str = APPROVED_50_SECOND_SCRIPT, duration: int = 50, master: bytes | None = None):
    from app.features.semantic_videos.service import compile_semantic_video_plan

    approved_master = master or _png_bytes()
    post, batch, reference = _snapshots(
        script=script,
        duration=duration,
        master=approved_master,
    )
    return compile_semantic_video_plan(
        post_snapshot=post,
        batch_snapshot=batch,
        reference_snapshot=reference,
        approved_frame_bytes=approved_master,
        price_per_provider_second=Decimal("0.40"),
        base_seed=240713,
    )


def test_compile_semantic_video_plan_builds_canonical_seven_take_costed_payload():
    compiled = _compile()

    assert len(APPROVED_50_SECOND_SCRIPT.split()) == 112
    assert len(compiled.take_payloads) == 7
    assert [take["take_index"] for take in compiled.take_payloads] == list(range(7))
    assert [take["shot_transform"]["name"] for take in compiled.take_payloads] == [
        "original",
        "center",
        "left",
        "right",
        "original",
        "center",
        "left",
    ]
    assert [take["provider_duration_seconds"] for take in compiled.take_payloads] == [8] * 7
    assert compiled.run_payload["plan_snapshot"]["take_count"] == 7
    assert compiled.run_payload["plan_snapshot"]["billable_provider_seconds"] == 56
    assert compiled.run_payload["plan_snapshot"]["quota_units"] == 7
    assert compiled.run_payload["plan_snapshot"]["price_per_provider_second_usd"] == "0.40"
    assert compiled.run_payload["plan_snapshot"]["estimated_cost_usd"] == "22.40"
    assert compiled.run_payload["estimated_cost_usd"] == "22.40"
    assert compiled.run_payload["plan_hash"] == compiled.plan_hash
    assert compiled.run_payload["plan_snapshot"]["visual_contract_hash"] == (
        compiled.run_payload["reference_snapshot"]["visual_contract"]["contract_hash"]
    )
    for take in compiled.take_payloads:
        request = take["request_contract"]
        assert request["visual_contract_hash"] == compiled.run_payload["plan_snapshot"][
            "visual_contract_hash"
        ]
        assert "the exact supplied garden patio" not in request["prompt"]
        assert "light-grey cardigan over a plain white top" not in request["prompt"]
        assert "input frame's subject, wardrobe, wheelchair, room" in request["prompt"]
        assert "cream knit sweater" not in request["prompt"]
    assert {take["seed"] for take in compiled.take_payloads} == {240713}
    assert re.fullmatch(r"[0-9a-f]{64}", compiled.plan_hash)
    json.dumps(compiled.run_payload, sort_keys=True)
    json.dumps(compiled.take_payloads, sort_keys=True)

    repeated = _compile()
    assert repeated.plan_hash == compiled.plan_hash
    assert repeated.run_payload == compiled.run_payload
    assert repeated.take_payloads == compiled.take_payloads


def test_compile_semantic_video_plan_accepts_manual_semantic_batch():
    from app.features.semantic_videos.service import compile_semantic_video_plan

    master = _png_bytes()
    post, batch, reference = _snapshots(master=master)
    batch["creation_mode"] = "manual_semantic_ugc"

    compiled = compile_semantic_video_plan(
        post_snapshot=post,
        batch_snapshot=batch,
        reference_snapshot=reference,
        approved_frame_bytes=master,
    )

    assert compiled.run_payload["requested_duration_seconds"] == 50
    assert compiled.run_payload["plan_snapshot"]["take_count"] == 7
    assert compiled.run_payload["script_snapshot"] == {
        "text": APPROVED_50_SECOND_SCRIPT,
        "review_status": "approved",
        "word_count": 112,
        "source": "manual_semantic_ugc",
        "creation_mode": "manual_semantic_ugc",
        "script_review_status": "approved",
        "target_duration_seconds": 50,
    }


def test_compile_semantic_video_plan_preserves_automated_semantic_provenance():
    compiled = _compile()

    assert compiled.run_payload["script_snapshot"] == {
        "text": APPROVED_50_SECOND_SCRIPT,
        "review_status": "approved",
        "word_count": 112,
        "source": "app.features.topics.semantic_scripts.generate_semantic_script",
        "creation_mode": "semantic_ugc",
        "script_review_status": "approved",
        "target_duration_seconds": 50,
    }


def test_compile_semantic_video_plan_hash_changes_with_script_master_or_duration():
    baseline = _compile()
    changed_script = _compile(script=APPROVED_50_SECOND_SCRIPT.replace("transparent", "nachvollziehbar"))
    changed_master = _compile(master=_png_bytes(accent=17))
    changed_duration = _compile(duration=51)

    assert len({baseline.plan_hash, changed_script.plan_hash, changed_master.plan_hash, changed_duration.plan_hash}) == 4


def test_compile_semantic_video_plan_never_loads_provider_magnific_or_lora_collaborators():
    forbidden_modules = {
        "app.adapters.magnific_client",
        "app.adapters.veo_client",
        "app.adapters.vertex_ai_client",
        "app.features.characters.scene_reference",
    }
    previously_loaded = {
        module_name: sys.modules[module_name]
        for module_name in forbidden_modules
        if module_name in sys.modules
    }
    try:
        for module_name in forbidden_modules:
            sys.modules.pop(module_name, None)

        _compile()

        assert forbidden_modules.isdisjoint(sys.modules)
    finally:
        # This test verifies plan compilation in isolation; it must not evict
        # modules imported during collection and poison later adapter tests.
        sys.modules.update(previously_loaded)
