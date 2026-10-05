"""Fresh-sheet script integrity and provenance guard regression checks."""
from pathlib import Path
import pytest
from scripts import run_runway_sheet_canary as canary


def test_dialogue_preserved_with_only_sheet_role_and_wheelchair(monkeypatch):
    dialogue = ' '.join(['Wort'] * 15)
    original = f'Old scene directions. Dialogue: “{dialogue}” Old source frame directions.'
    monkeypatch.setattr(canary, 'SCRIPT_PROMPT_SHA', canary.digest(original.encode()))
    prompt, extracted = canary.build_prompt(original)
    assert extracted == dialogue
    assert f'Dialogue: “{dialogue}”' in prompt
    assert 'sole actor identity' in prompt and 'stays seated' in prompt
    assert 'Old scene' not in prompt and 'Old source' not in prompt
    assert '@Image 2' not in prompt


def test_changed_script_cannot_enter_paid_submission():
    with pytest.raises(ValueError, match='Frozen script source changed'):
        canary.build_prompt('different saved script')


def test_changed_sheet_cannot_enter_paid_submission(tmp_path):
    path=tmp_path/'changed.png'; path.write_bytes(b'changed image')
    with pytest.raises(ValueError, match='checksum changed'):
        canary.sheet_bytes(path)
