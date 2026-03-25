"""Deepgram Nova-2 adapter for word-level German transcription."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

DEEPGRAM_API_URL = "https://api.deepgram.com/v1/listen"


class DeepgramError(Exception):
    def __init__(self, message: str, *, transient: bool = False, details: Optional[dict] = None):
        super().__init__(message)
        self.transient = transient
        self.details = details or {}


@dataclass
class Word:
    word: str
    start: float
    end: float


@dataclass
class WordLevelTranscript:
    words: list[Word]
    full_text: str


class DeepgramClient:
    _instance: Optional["DeepgramClient"] = None

    def __new__(cls) -> "DeepgramClient":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        settings = get_settings()
        self._api_key = settings.deepgram_api_key
        self._client = httpx.Client(timeout=60.0)
        self._initialized = True

    def transcribe(self, *, audio_bytes: bytes, correlation_id: str, language: str = "de") -> WordLevelTranscript:
        logger.info("deepgram_transcribe_start", correlation_id=correlation_id, bytes_len=len(audio_bytes), language=language)
        params = {"model": "nova-2", "smart_format": "true", "language": language}
        headers = {"Authorization": f"Token {self._api_key}", "Content-Type": "audio/mp4"}
        try:
            response = self._client.post(DEEPGRAM_API_URL, params=params, headers=headers, content=audio_bytes)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise DeepgramError(
                f"Deepgram API error {status}",
                transient=status >= 500,
                details={"status_code": status, "correlation_id": correlation_id},
            ) from exc
        except httpx.RequestError as exc:
            raise DeepgramError(
                f"Deepgram request failed: {exc}",
                transient=True,
                details={"correlation_id": correlation_id},
            ) from exc
        raw = response.json()
        transcript = self._parse_response(raw)
        logger.info("deepgram_transcribe_done", correlation_id=correlation_id, word_count=len(transcript.words), has_audio=len(transcript.words) > 0)
        return transcript

    def _parse_response(self, raw: dict[str, Any]) -> WordLevelTranscript:
        channels = raw.get("results", {}).get("channels", [])
        if not channels:
            return WordLevelTranscript(words=[], full_text="")
        alt = channels[0].get("alternatives", [{}])[0]
        full_text = alt.get("transcript", "")
        raw_words = alt.get("words", [])
        words = [Word(word=w["word"], start=w["start"], end=w["end"]) for w in raw_words]
        return WordLevelTranscript(words=words, full_text=full_text)


def get_deepgram_client() -> DeepgramClient:
    return DeepgramClient()
