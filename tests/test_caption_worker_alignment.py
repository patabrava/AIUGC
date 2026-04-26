"""Test that caption worker uses script alignment."""

from unittest.mock import MagicMock, patch

from app.adapters.deepgram_client import Word, WordLevelTranscript


def test_caption_worker_aligns_transcript_to_script():
    """The caption worker should align Deepgram output to seed_data.script."""
    from workers.caption_worker import _process_caption_post

    fake_post = {
        "id": "test-post-id",
        "batch_id": "test-batch-id",
        "video_url": "https://example.com/video.mp4",
        "video_metadata": {},
        "seed_data": {"script": "Ab Juli wird"},
    }

    mock_transcript = WordLevelTranscript(
        words=[
            Word(word="ab", start=0.5, end=0.7),
            Word(word="Julie", start=0.8, end=1.1),
            Word(word="wird", start=1.2, end=1.4),
        ],
        full_text="ab Julie wird",
    )

    with (
        patch("workers.caption_worker.get_supabase") as mock_sb,
        patch("workers.caption_worker.get_storage_client") as mock_storage,
        patch("workers.caption_worker.get_deepgram_client") as mock_dg,
        patch("workers.caption_worker.burn_captions") as mock_burn,
        patch("workers.caption_worker._mark_caption_completed"),
        patch("workers.caption_worker._check_batch_caption_complete"),
        patch("builtins.open", MagicMock()),
        patch("os.close"),
        patch("os.unlink"),
        patch("tempfile.mkstemp", return_value=(0, "/tmp/fake.mp4")),
    ):
        mock_sb.return_value.client.table.return_value.update.return_value.eq.return_value.execute.return_value = None
        mock_storage.return_value.download_video.return_value = b"fake-video-bytes"
        mock_storage.return_value.upload_video.return_value = {
            "url": "https://example.com/captioned.mp4",
            "storage_key": "test-key",
            "size": 100,
        }
        mock_dg.return_value.transcribe.return_value = mock_transcript
        mock_burn.return_value = "/tmp/fake_output.mp4"

        _process_caption_post(fake_post)

        burn_call = mock_burn.call_args
        transcript_arg = burn_call.kwargs.get("transcript") or burn_call[1].get("transcript")
        aligned_words = [w.word for w in transcript_arg.words]
        assert "Juli" in aligned_words, f"Expected 'Juli' in aligned words, got {aligned_words}"
        assert "Julie" not in aligned_words


def test_caption_worker_uses_bounded_post_projection(monkeypatch):
    """The caption poller should not fetch full post rows on every idle pass."""
    selected_fields = {}

    class FakeTable:
        def __init__(self, name):
            self._name = name

        def select(self, fields, *args, **kwargs):
            selected_fields[self._name] = fields
            return self

        def in_(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def execute(self):
            return MagicMock(data=[])

    class FakeSupabase:
        client = MagicMock()

    fake_sb = FakeSupabase()
    fake_sb.client.table = lambda name: FakeTable(name)
    monkeypatch.setattr("workers.caption_worker.get_supabase", lambda: fake_sb)

    from workers.caption_worker import poll_caption_pending

    poll_caption_pending()

    assert selected_fields["posts"] != "*"
    assert "video_status" not in selected_fields["posts"]
    assert "publish_results" not in selected_fields["posts"]
