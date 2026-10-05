"""Protect the standalone paid canary against duplicate and changed-source work."""
import json
import os

import pytest

from scripts.run_runway_new_character_canary import digest, fence, save, validate_source
from scripts import run_runway_new_character_canary as canary


@pytest.mark.parametrize("status", ["submission_intent", "submission_unknown", "submitted", "RUNNING", "FAILED", "SUCCEEDED"])
def test_existing_submission_is_fenced(status):
    manifest = {"video": {"status": status, "task_id": "existing-task"}}
    with pytest.raises(ValueError, match="never resubmit"):
        fence(manifest, "video")
    assert manifest["video"] == {"status": status, "task_id": "existing-task"}


def test_submission_intent_survives_process_restart(tmp_path):
    manifest = {}
    fence(manifest, "video")
    path = tmp_path / "manifest.json"
    save(path, manifest)
    recovered = json.loads(path.read_text())
    assert os.stat(path).st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="never resubmit"):
        fence(recovered, "video")


def new_source():
    content = b"new original provider bytes"
    manifest = {"model": "seedance2_5", "source_mode": "text_only_new_character",
                "image": {"status": "SUCCEEDED", "sha256": digest(content)}}
    return manifest, content


def test_changed_original_and_unreviewed_source_cannot_submit():
    manifest, content = new_source()
    validate_source(manifest, content, True)
    with pytest.raises(ValueError, match="Source changed"):
        validate_source(manifest, content + b"edited", True)
    with pytest.raises(ValueError, match="visually reviewed"):
        validate_source(manifest, content, False)


@pytest.mark.parametrize("field,value", [("model", "wan3_0"), ("source_mode", "reference_guided")])
def test_alternative_model_or_source_contract_cannot_submit(field, value):
    manifest, content = new_source()
    manifest[field] = value
    with pytest.raises(ValueError, match="contract changed"):
        validate_source(manifest, content, True)


def test_known_rejected_image_and_failed_image_are_blocked(monkeypatch):
    manifest, content = new_source()
    monkeypatch.setattr(canary, "REJECTED_SOURCES", {digest(content)})
    with pytest.raises(ValueError, match="already rejected"):
        validate_source(manifest, content, True)
    manifest["image"]["status"] = "FAILED"
    with pytest.raises(ValueError, match="successful"):
        validate_source(manifest, content, True)


def test_native_text_path_preserves_dialogue_and_has_no_absent_image_authority():
    dialogue = " ".join(["Wort"] * 15)
    prompt, extracted = canary.native_text_prompt(f'Other source directions.\nDialogue: “{dialogue}”\nAudio direction.')
    assert extracted == dialogue
    assert f'Dialogue: “{dialogue}”' in prompt
    assert "fictional adult presenter" in prompt
    assert "source frame" not in prompt
    with pytest.raises(ValueError, match="uniquely"):
        canary.native_text_prompt(f'Dialogue: “{dialogue}” Dialogue: “{dialogue}”')
