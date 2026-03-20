from fastapi.testclient import TestClient

from app.main import app


def test_tiktok_url_verification_file_is_served_at_root():
    client = TestClient(app)

    response = client.get("/tiktokM1iYTqs7dJ1raJALxFS3sJhodU2gFDuk.txt")

    assert response.status_code == 200
    assert response.text.strip() == "tiktok-developers-site-verification=M1iYTqs7dJ1raJALxFS3sJhodU2gFDuk"
    assert response.headers["content-type"].startswith("text/plain")
