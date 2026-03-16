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
    monkeypatch.setattr(
        veo_module.genai,
        "Client",
        lambda api_key: SimpleNamespace(api_key=api_key),
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
    )

    payload = fake_http_client.post_calls[0]["json"]
    assert payload["parameters"]["aspectRatio"] == "9:16"
    assert payload["parameters"]["resolution"] == "720p"
    assert payload["parameters"]["negativePrompt"] == "subtitles, watermark"
    assert payload["instances"][0]["prompt"] == "portrait product demo"
    assert submission["operation_id"] == "operations/test-operation"

    veo_module.VeoClient._instance = None
