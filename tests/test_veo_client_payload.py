import base64
from types import SimpleNamespace

import app.adapters.veo_client as veo_module


class FakeHttpClient:
    def __init__(self):
        self.post_calls = []

    def post(self, url, headers, json):
        self.post_calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
            }
        )
        return SimpleNamespace(
            status_code=200,
            text='{"name":"operations/test-operation"}',
            raise_for_status=lambda: None,
            json=lambda: {"name": "operations/test-operation"},
        )


def test_veo_submission_includes_aspect_ratio_resolution_and_negative_prompt(monkeypatch):
    monkeypatch.setattr(
        veo_module,
        "get_settings",
        lambda: SimpleNamespace(google_ai_api_key="test-key"),
    )
    fake_http_client = FakeHttpClient()
    veo_module.VeoClient._instance = None
    client = veo_module.VeoClient()
    client._http_client = fake_http_client

    submission = client.submit_video_generation(
        prompt="portrait product demo",
        negative_prompt="subtitles, watermark",
        correlation_id="test-correlation",
        aspect_ratio="9:16",
        resolution="720p",
        duration_seconds=8,
    )

    payload = fake_http_client.post_calls[0]["json"]
    assert payload["parameters"]["aspectRatio"] == "9:16"
    assert payload["parameters"]["resolution"] == "720p"
    assert payload["parameters"]["durationSeconds"] == 8
    assert payload["parameters"]["negativePrompt"] == "subtitles, watermark"
    assert payload["instances"][0]["prompt"] == "portrait product demo"
    assert submission["operation_id"] == "operations/test-operation"

    veo_module.VeoClient._instance = None


def test_veo_extension_uses_rest_video_uri_payload(monkeypatch):
    monkeypatch.setattr(
        veo_module,
        "get_settings",
        lambda: SimpleNamespace(google_ai_api_key="test-key"),
    )
    fake_http_client = FakeHttpClient()
    veo_module.VeoClient._instance = None
    client = veo_module.VeoClient()
    client._http_client = fake_http_client

    submission = client.submit_video_extension(
        prompt="continue the scene",
        video_uri="gs://bucket-name/example.mp4",
        correlation_id="test-extension",
        aspect_ratio="9:16",
        resolution="720p",
        negative_prompt="subtitles, watermark",
    )

    payload = fake_http_client.post_calls[0]["json"]
    assert payload["parameters"]["aspectRatio"] == "9:16"
    assert payload["parameters"]["resolution"] == "720p"
    assert payload["parameters"]["durationSeconds"] == 8
    assert payload["parameters"]["negativePrompt"] == "subtitles, watermark"
    assert payload["instances"][0]["prompt"] == "continue the scene"
    assert payload["instances"][0]["video"]["uri"] == "gs://bucket-name/example.mp4"
    assert submission["operation_id"] == "operations/test-operation"

    veo_module.VeoClient._instance = None


def test_veo_submission_includes_first_frame_inline_image(monkeypatch):
    monkeypatch.setattr(
        veo_module,
        "get_settings",
        lambda: SimpleNamespace(google_ai_api_key="test-key"),
    )
    fake_http_client = FakeHttpClient()
    veo_module.VeoClient._instance = None
    client = veo_module.VeoClient()
    client._http_client = fake_http_client

    anchor_bytes = b"sarah-anchor-image"
    submission = client.submit_video_generation(
        prompt="portrait product demo",
        negative_prompt="subtitles, watermark",
        correlation_id="test-correlation",
        aspect_ratio="9:16",
        resolution="720p",
        duration_seconds=8,
        first_frame_image={
            "mime_type": "image/jpeg",
            "data_base64": base64.b64encode(anchor_bytes).decode("ascii"),
        },
    )

    payload = fake_http_client.post_calls[0]["json"]
    inline_data = payload["instances"][0]["image"]["inlineData"]
    assert inline_data["mimeType"] == "image/jpeg"
    assert base64.b64decode(inline_data["data"]) == anchor_bytes
    assert payload["parameters"]["aspectRatio"] == "9:16"
    assert payload["parameters"]["resolution"] == "720p"
    assert payload["parameters"]["durationSeconds"] == 8
    assert submission["operation_id"] == "operations/test-operation"

    veo_module.VeoClient._instance = None
