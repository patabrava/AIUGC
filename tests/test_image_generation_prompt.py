from __future__ import annotations

import ast
from pathlib import Path

from app.core.image_generation_prompt import (
    RAW_CAMERA_SYSTEM_PROMPT_PATH,
    load_raw_camera_system_prompt,
    write_raw_camera_image_prompt,
)


def test_canonical_image_system_prompt_is_loaded_literally_from_source_file():
    canonical = RAW_CAMERA_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    assert load_raw_camera_system_prompt() == canonical
    assert canonical.startswith(
        "You are a platform neutral image prompt writer for Nano Banana Pro and GPT Image Gen V2."
    )
    assert "FINAL INSTRUCTION" in canonical


def test_prompt_writer_uses_literal_contract_and_returns_renderer_prompt():
    class Client:
        def __init__(self):
            self.call = None

        def generate_gemini_text(self, **kwargs):
            self.call = kwargs
            return "An ordinary raw-camera portrait in a blue shirt."

    client = Client()
    output = write_raw_camera_image_prompt(client=client, brief="Use a blue shirt.")

    assert output == "An ordinary raw-camera portrait in a blue shirt."
    assert client.call == {
        "prompt": "Use a blue shirt.",
        "system_prompt": load_raw_camera_system_prompt(),
        "max_tokens": 4096,
        "temperature": 0.2,
        "thinking_budget": 0,
    }


def test_feature_renderers_never_receive_the_prompt_writer_system_instruction():
    repo_root = Path(__file__).resolve().parents[1]
    invalid: list[str] = []
    found = 0

    for path in (repo_root / "app" / "features").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not (
                isinstance(function, ast.Attribute)
                and function.attr == "generate_gemini_image"
            ):
                continue
            found += 1
            if any(keyword.arg == "system_prompt" for keyword in node.keywords):
                invalid.append(str(path.relative_to(repo_root)))

    assert found == 5
    assert invalid == []


def test_all_image_feature_paths_invoke_the_raw_camera_prompt_writer():
    repo_root = Path(__file__).resolve().parents[1]
    feature_paths = [
        "app/features/scenes/background_comparison.py",
        "app/features/characters/reference_generation.py",
        "app/features/blog/blog_runtime.py",
        "app/features/shot_frames/service.py",
        "app/features/shot_frames/wheelchair_scene_plate.py",
    ]

    for relative_path in feature_paths:
        source = (repo_root / relative_path).read_text(encoding="utf-8")
        assert (
            "write_raw_camera_image_prompt(" in source
            or "system_prompt=load_raw_camera_system_prompt()" in source
        ), relative_path
