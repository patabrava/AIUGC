from pathlib import Path


def test_accounts_hub_tiktok_copy_does_not_show_sandbox_or_draft_only():
    template = Path("templates/components/accounts_hub.html").read_text(encoding="utf-8").lower()

    assert "sandbox" not in template
    assert "draft only" not in template
    assert "tiktokdirectpostavailable" in template
    assert "can post directly from lippe lift studio" in template
