from __future__ import annotations

import os

import pytest

from app.adapters.magnific_client import get_magnific_client
from app.core.config import get_settings


pytestmark = pytest.mark.skipif(
    os.getenv("AIUGC_LIVE_MAGNIFIC_SMOKE") != "1",
    reason="Paid Magnific smoke requires AIUGC_LIVE_MAGNIFIC_SMOKE=1",
)


def test_live_magnific_lists_loras():
    assert get_settings().magnific_api_key, "MAGNIFIC_API_KEY is required"
    response = get_magnific_client().list_loras(correlation_id="live-magnific-list-loras")
    assert "data" in response
