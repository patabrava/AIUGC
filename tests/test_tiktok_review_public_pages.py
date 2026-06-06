from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_root_serves_public_product_page_without_auth():
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert "Lippe Lift Studio" in response.text
    assert "creator" in response.text.lower()
    assert "/auth/login" in response.text
    assert "/terms" in response.text
    assert "/privacy" in response.text
    assert "Only @lippelift.de emails" not in response.text


def test_public_page_is_not_a_login_or_private_dashboard():
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert "Email address" not in response.text
    assert "Batches" not in response.text
    assert "Topics" not in response.text


def test_terms_do_not_describe_app_as_internal_only():
    response = client.get("/terms")

    assert response.status_code == 200
    text = response.text.lower()
    assert "terms of service" in text
    assert "internal use" not in text
    assert "exclusively" not in text
    assert "@lippelift.de" not in text
    assert "creator" in text
    assert "tiktok" in text


def test_privacy_policy_describes_tiktok_data_handling():
    response = client.get("/privacy")

    assert response.status_code == 200
    text = response.text.lower()
    assert "privacy policy" in text
    assert "tiktok" in text
    assert "access token" in text
    assert "video" in text
    assert "delete" in text or "disconnect" in text


def test_authenticated_shell_footer_has_visible_terms_and_privacy_links():
    template = Path("templates/base.html").read_text(encoding="utf-8")

    assert 'href="/terms"' in template
    assert "Terms of Service" in template
    assert 'href="/privacy"' in template
    assert "Privacy Policy" in template
