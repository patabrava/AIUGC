import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import app.adapters.deepgram_client as deepgram_module


@pytest.fixture(autouse=True)
def _mock_settings(monkeypatch):
    monkeypatch.setattr(
        deepgram_module,
        "get_settings",
        lambda: SimpleNamespace(deepgram_api_key="test-key"),
    )

from app.adapters.deepgram_client import (
    DeepgramClient,
    Word,
    WordLevelTranscript,
    DeepgramError,
    get_deepgram_client,
)


class TestWordLevelTranscript:
    def test_word_dataclass(self):
        w = Word(word="Hallo", start=0.0, end=0.5)
        assert w.word == "Hallo"
        assert w.start == 0.0
        assert w.end == 0.5

    def test_transcript_dataclass(self):
        words = [Word("Hallo", 0.0, 0.5), Word("Welt", 0.6, 1.0)]
        t = WordLevelTranscript(words=words, full_text="Hallo Welt")
        assert len(t.words) == 2
        assert t.full_text == "Hallo Welt"

    def test_empty_transcript(self):
        t = WordLevelTranscript(words=[], full_text="")
        assert t.words == []
        assert t.full_text == ""


class TestDeepgramClientParsing:
    def test_parse_response_success(self):
        DeepgramClient._instance = None
        client = DeepgramClient()
        raw = {
            "results": {
                "channels": [{
                    "alternatives": [{
                        "transcript": "Mach diesen Fehler nicht",
                        "words": [
                            {"word": "Mach", "start": 0.0, "end": 0.3, "confidence": 0.99},
                            {"word": "diesen", "start": 0.35, "end": 0.6, "confidence": 0.98},
                            {"word": "Fehler", "start": 0.65, "end": 1.0, "confidence": 0.97},
                            {"word": "nicht", "start": 1.05, "end": 1.3, "confidence": 0.99},
                        ]
                    }]
                }]
            }
        }
        transcript = client._parse_response(raw)
        assert len(transcript.words) == 4
        assert transcript.words[0].word == "Mach"
        assert transcript.words[2].end == 1.0
        assert transcript.full_text == "Mach diesen Fehler nicht"

    def test_parse_response_empty_audio(self):
        DeepgramClient._instance = None
        client = DeepgramClient()
        raw = {
            "results": {
                "channels": [{
                    "alternatives": [{
                        "transcript": "",
                        "words": []
                    }]
                }]
            }
        }
        transcript = client._parse_response(raw)
        assert transcript.words == []
        assert transcript.full_text == ""


class TestDeepgramClientTranscribe:
    def test_transcribe_success(self):
        DeepgramClient._instance = None
        client = DeepgramClient()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": {
                "channels": [{
                    "alternatives": [{
                        "transcript": "Hallo",
                        "words": [{"word": "Hallo", "start": 0.0, "end": 0.5, "confidence": 0.99}]
                    }]
                }]
            }
        }
        mock_response.raise_for_status = MagicMock()
        client._client = MagicMock()
        client._client.post.return_value = mock_response
        result = client.transcribe(audio_bytes=b"fake_audio", correlation_id="test_1")
        assert result.full_text == "Hallo"
        assert len(result.words) == 1

    def test_transcribe_api_error_raises_deepgram_error(self):
        import httpx
        DeepgramClient._instance = None
        client = DeepgramClient()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_request = MagicMock(spec=httpx.Request)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Request", request=mock_request, response=mock_response
        )
        client._client = MagicMock()
        client._client.post.return_value = mock_response
        with pytest.raises(DeepgramError) as exc_info:
            client.transcribe(audio_bytes=b"bad", correlation_id="test_2")
        assert exc_info.value.transient is False

    def test_transcribe_5xx_is_transient(self):
        import httpx
        DeepgramClient._instance = None
        client = DeepgramClient()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 503
        mock_request = MagicMock(spec=httpx.Request)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Service Unavailable", request=mock_request, response=mock_response
        )
        client._client = MagicMock()
        client._client.post.return_value = mock_response
        with pytest.raises(DeepgramError) as exc_info:
            client.transcribe(audio_bytes=b"bad", correlation_id="test_3")
        assert exc_info.value.transient is True
