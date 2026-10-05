from fastapi.testclient import TestClient

from app.main import TIKTOK_VERIFICATION_FILENAME, app


def test_tiktok_url_verification_file_is_served_at_root():
    client = TestClient(app)

    response = client.get(f"/{TIKTOK_VERIFICATION_FILENAME}")

    assert response.status_code == 200
    expected_token = TIKTOK_VERIFICATION_FILENAME.removeprefix("tiktok").removesuffix(".txt")
    assert response.text.strip() == f"tiktok-developers-site-verification={expected_token}"
    assert response.headers["content-type"].startswith("text/plain")
