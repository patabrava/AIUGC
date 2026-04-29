from pathlib import Path
from typing import Optional
from types import SimpleNamespace

from app.core.config import Settings, resolve_google_application_credentials_path
from app.adapters.vertex_ai_client import VertexSettings


def _write_minimal_env(tmp_path: Path, extra_lines: Optional[list[str]] = None) -> None:
    lines = [
        "SUPABASE_URL=https://example.supabase.co",
        "SUPABASE_KEY=x",
        "SUPABASE_SERVICE_KEY=y",
        "CLOUDFLARE_R2_PUBLIC_BASE_URL=https://r2.example.com",
    ]
    if extra_lines:
        lines.extend(extra_lines)
    (tmp_path / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_vertex_settings_default_to_disabled(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    _write_minimal_env(tmp_path)

    settings = Settings()
    assert settings.vertex_ai_project_id == ""
    assert settings.vertex_ai_location == "us-central1"
    assert settings.vertex_ai_enabled is False


def test_gemini_provider_defaults_to_vertex_without_legacy_fallback(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    _write_minimal_env(tmp_path)

    settings = Settings()

    assert settings.gemini_provider == "vertex"
    assert settings.gemini_api_fallback_enabled is False
    assert settings.gemini_deep_research_provider == "vertex_grounded"
    assert settings.vertex_gemini_model == "gemini-2.5-flash"
    assert settings.vertex_gemini_image_model == "gemini-2.5-flash-image"
    assert settings.vertex_grounded_research_model == "gemini-2.5-pro"
    assert settings.vertex_grounded_research_location == "global"


def test_gemini_provider_accepts_legacy_fallback(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    _write_minimal_env(
        tmp_path,
        [
            "GEMINI_PROVIDER=gemini_api",
            "GEMINI_API_FALLBACK_ENABLED=true",
            "GEMINI_DEEP_RESEARCH_PROVIDER=gemini_api",
            "VERTEX_GEMINI_MODEL=gemini-2.5-pro",
            "VERTEX_GROUNDED_RESEARCH_LOCATION=us-central1",
        ],
    )

    settings = Settings()

    assert settings.gemini_provider == "gemini_api"
    assert settings.gemini_api_fallback_enabled is True
    assert settings.gemini_deep_research_provider == "gemini_api"
    assert settings.vertex_gemini_model == "gemini-2.5-pro"
    assert settings.vertex_grounded_research_location == "us-central1"


def test_veo_reference_image_toggle_defaults_to_disabled(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VEO_USE_REFERENCE_IMAGE", raising=False)
    _write_minimal_env(tmp_path)

    settings = Settings()

    assert settings.veo_use_reference_image is False


def test_veo_reference_image_toggle_accepts_false(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    _write_minimal_env(tmp_path, ["VEO_USE_REFERENCE_IMAGE=false"])

    settings = Settings()

    assert settings.veo_use_reference_image is False


def test_vertex_settings_use_explicit_project_and_location(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    _write_minimal_env(
        tmp_path,
        [
            "VERTEX_AI_PROJECT_ID=my-project",
            "VERTEX_AI_LOCATION=europe-west4",
            "VERTEX_AI_ENABLED=true",
        ],
    )

    settings = Settings()
    assert settings.vertex_ai_project_id == "my-project"
    assert settings.vertex_ai_location == "europe-west4"
    assert settings.vertex_ai_enabled is True


def test_google_application_credentials_alias_is_resolved(monkeypatch, tmp_path: Path):
    adc_path = tmp_path / "adc.json"
    adc_path.write_text("{}", encoding="utf-8")
    settings = SimpleNamespace(google_application_credentials=str(adc_path))

    assert resolve_google_application_credentials_path(settings) == str(adc_path)


def test_vertex_settings_resolve_google_application_credentials_from_env_file(monkeypatch, tmp_path: Path):
    adc_path = tmp_path / "adc.json"
    adc_path.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(adc_path))
    settings = VertexSettings()

    assert resolve_google_application_credentials_path(settings) == str(adc_path)


def test_vertex_settings_materialize_google_application_credentials_json(monkeypatch, tmp_path: Path):
    adc_json = '{"type":"authorized_user","client_id":"abc","client_secret":"def","refresh_token":"ghi"}'
    monkeypatch.chdir(tmp_path)
    _write_minimal_env(
        tmp_path,
        [
            "VERTEX_AI_ENABLED=true",
            "VERTEX_AI_PROJECT_ID=my-project",
            f"GOOGLE_APPLICATION_CREDENTIALS_JSON={adc_json}",
        ],
    )

    settings = VertexSettings()
    resolved = resolve_google_application_credentials_path(settings)

    assert resolved is not None
    materialized = Path(resolved)
    assert materialized.is_file()
    assert materialized.read_text(encoding="utf-8") == adc_json
