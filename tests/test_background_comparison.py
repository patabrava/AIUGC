from __future__ import annotations

from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image


class FakeBackgroundLLMClient:
    def __init__(self):
        self.text_calls: list[dict] = []
        self.image_calls: list[dict] = []

    def generate_gemini_text(self, **kwargs):
        self.text_calls.append(kwargs)
        return (
            "A vertical unretouched camera photograph of the exact actor-free living room, with ordinary window "
            "light, believable oak and ceramic textures, slight optical softness, muted color, no people, no text, "
            "no HDR glow, and no stylization."
        )

    def generate_gemini_image(self, **kwargs):
        self.image_calls.append(kwargs)
        return {
            "image_bytes": b"raw-camera-background",
            "mime_type": "image/png",
            "model": "gemini-3.1-flash-image",
        }


def test_background_brief_preserves_scene_and_is_strictly_actor_free():
    from app.features.scenes.background_comparison import build_raw_camera_background_brief

    brief = build_raw_camera_background_brief("home_living_room_advice_a")

    assert "quiet modern living room" in brief
    assert "narrow light-oak side table" in brief
    assert "Soft window light from left side" in brief
    assert "No people, faces, bodies, body parts, hands, or wheelchairs" in brief
    assert "environment-only" in brief
    assert "subject and table context visible" not in brief
    assert "hands visible" not in brief


def test_generate_raw_camera_background_uses_long_prompt_then_generates_one_image():
    from app.features.scenes.background_comparison import generate_raw_camera_background

    client = FakeBackgroundLLMClient()
    result = generate_raw_camera_background(
        scene_key="home_living_room_advice_a",
        llm_client=client,
    )

    assert len(client.text_calls) == 1
    assert client.text_calls[0]["system_prompt"].startswith("You are a platform neutral image prompt writer")
    assert "Do not generate the image." in client.text_calls[0]["system_prompt"]
    assert client.text_calls[0]["temperature"] == 0.2
    assert client.text_calls[0]["thinking_budget"] == 0
    assert len(client.image_calls) == 1
    assert client.image_calls[0] == {
        "prompt": result.prompt_writer_output,
        "model": "gemini-3.1-flash-image",
        "temperature": 0.7,
        "aspect_ratio": "9:16",
        "image_size": "2K",
    }
    assert result.image_bytes == b"raw-camera-background"
    assert result.mime_type == "image/png"
    assert result.provider_model == "gemini-3.1-flash-image"


def _png_bytes(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def test_compose_side_by_side_normalizes_portrait_images_without_distortion():
    from app.features.scenes.background_comparison import compose_side_by_side

    rendered = compose_side_by_side(
        control_bytes=_png_bytes((540, 960), (220, 210, 190)),
        treatment_bytes=_png_bytes((1024, 1792), (180, 170, 150)),
        scene_name="Home living room advice A",
    )

    with Image.open(BytesIO(rendered)) as image:
        assert image.format == "PNG"
        assert image.size == (1536, 1461)


def test_render_comparison_index_labels_both_prompts_and_all_scenes():
    from app.features.scenes.background_comparison import render_comparison_index

    html = render_comparison_index(
        [
            {
                "scene_key": "home_living_room_advice_a",
                "scene_name": "Home living room advice A",
                "control_path": "home/current.png",
                "treatment_path": "home/raw-camera.png",
                "comparison_path": "home/side-by-side.png",
            },
            {
                "scene_key": "car_transfer_residential_a",
                "scene_name": "Residential car transfer A",
                "control_path": "car/current.png",
                "treatment_path": "car/raw-camera.png",
                "comparison_path": "car/side-by-side.png",
            },
        ]
    )

    assert html.startswith("<!doctype html>")
    assert '<link rel="icon" href="data:,">' in html
    assert "Current · Reality-First" in html
    assert "Test · Raw Camera Casting Realism" in html
    assert "home/side-by-side.png" in html
    assert "car/side-by-side.png" in html
    assert "Physical realism" in html
    assert "Scene-layout fidelity" in html


def test_live_comparison_script_defaults_and_writes_only_local_artifacts(tmp_path):
    from app.features.scenes.background_comparison import RawCameraBackgroundResult
    from scripts.compare_raw_camera_backgrounds import DEFAULT_SCENE_KEYS, run_comparison

    assert DEFAULT_SCENE_KEYS == (
        "home_living_room_advice_a",
        "bathroom_accessibility_a",
        "car_transfer_residential_a",
    )
    control = _png_bytes((540, 960), (220, 210, 190))
    treatment = _png_bytes((1024, 1792), (180, 170, 150))

    def load_asset(scene_key: str):
        return SimpleNamespace(
            id=f"asset-{scene_key}",
            scene_key=scene_key,
            scene_bible_version=1,
            status="generated",
            image_url=f"https://example.com/{scene_key}.png",
            provider_model="gemini-3-pro-image-preview",
            system_prompt_name="reality_first_prompt_v1",
        )

    def generate(*, scene_key: str):
        return RawCameraBackgroundResult(
            scene_key=scene_key,
            prompt_writer_brief=f"brief for {scene_key}",
            prompt_writer_output=f"finished prompt for {scene_key}.",
            image_bytes=treatment,
            mime_type="image/png",
            provider_model="gemini-3.1-flash-image",
        )

    run_dir, failures = run_comparison(
        scene_keys=("home_living_room_advice_a",),
        output_root=tmp_path,
        load_asset=load_asset,
        download_image=lambda _url: control,
        generate=generate,
        run_name="test-run",
    )

    assert failures == []
    assert run_dir == tmp_path / "test-run"
    assert (run_dir / "home_living_room_advice_a" / "current.png").is_file()
    assert (run_dir / "home_living_room_advice_a" / "raw-camera.png").is_file()
    assert (run_dir / "home_living_room_advice_a" / "side-by-side.png").is_file()
    assert (run_dir / "index.html").is_file()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["production_assets_updated"] is False
    assert manifest["scenes"][0]["control"]["asset_id"].startswith("asset-")
    assert manifest["scenes"][0]["treatment"]["provider_model"] == "gemini-3.1-flash-image"


def test_live_comparison_script_rejects_missing_control_asset(tmp_path):
    from scripts.compare_raw_camera_backgrounds import run_comparison

    run_dir, failures = run_comparison(
        scene_keys=("home_living_room_advice_a",),
        output_root=tmp_path,
        load_asset=lambda _scene_key: None,
        download_image=lambda _url: b"unused",
        generate=lambda **_kwargs: None,
        run_name="missing-control",
    )

    assert failures == ["home_living_room_advice_a"]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["scenes"][0]["status"] == "failed"
    assert "generated control asset" in manifest["scenes"][0]["error"]
