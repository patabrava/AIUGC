"""Just-in-time script contracts for the Semantic UGC production mode."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.adapters.llm_client import get_llm_client
from app.core.video_profiles import script_word_count
from app.features.shot_production.duration import (
    SemanticDurationContract,
    build_semantic_duration_contract,
)
from app.features.shot_production.planner import plan_editorial_beats


PROMPT_DATA_DIR = Path(__file__).resolve().parent / "prompt_data"
SUPPORTED_POST_TYPES = frozenset({"value", "lifestyle", "product"})
_RESPONSE_LABEL = re.compile(
    r"^(?:script|skript|voice[ -]?over|gesprochener\s+text)\s*:\s*",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD_PATTERN = re.compile(
    r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß]+(?:[.-][A-Za-zÀ-ÿ0-9ÄÖÜäöüß]+)*"
)

SEMANTIC_SCRIPT_SYSTEM_PROMPT = """Du schreibst natürlich gesprochene UGC-Skripte auf Basis belegter Fakten.
Gib ausschließlich den finalen Sprechtext aus. Jeder Satz trägt eine neue vollständige Aussage bei.
Halte die angegebene Wortspanne und die semantische Take-Struktur exakt ein."""


@dataclass(frozen=True)
class SemanticScriptResult:
    script: str
    contract_hash: str
    provenance: Mapping[str, Any]


@lru_cache(maxsize=None)
def _load_semantic_template(post_type: str) -> str:
    path = PROMPT_DATA_DIR / f"semantic_{post_type}.txt"
    return path.read_text(encoding="utf-8").strip()


def _normalize_post_type(post_type: str) -> str:
    normalized = str(post_type or "").strip().lower()
    if normalized not in SUPPORTED_POST_TYPES:
        allowed = ", ".join(sorted(SUPPORTED_POST_TYPES))
        raise ValueError(f"Semantic UGC post_type must be one of: {allowed}.")
    return normalized


def _normalize_facts(facts: Optional[Iterable[str]]) -> tuple[str, ...]:
    return tuple(
        text
        for value in facts or ()
        if (text := " ".join(str(value or "").split()))
    )


def build_semantic_script_prompt(
    *,
    post_type: str,
    title: str,
    cta: str,
    facts: Optional[Iterable[str]],
    requested_duration_seconds: int,
    language: str = "Deutsch",
    actor_context: Optional[str] = None,
    maximum_seconds: Optional[int] = None,
) -> str:
    """Render one of three generic family prompts with a canonical duration contract."""
    normalized_post_type = _normalize_post_type(post_type)
    contract = build_semantic_duration_contract(
        requested_duration_seconds,
        maximum_seconds=maximum_seconds,
    )
    fact_values = _normalize_facts(facts)
    fact_lines = "\n".join(f"- {fact}" for fact in fact_values) or "- Keine Zusatzfakten."
    template = _load_semantic_template(normalized_post_type)
    return template.format(
        requested_duration_seconds=contract.requested_duration_seconds,
        delivery_min_seconds=contract.delivery_min_seconds,
        delivery_max_seconds=contract.delivery_max_seconds,
        minimum_words=contract.minimum_words,
        maximum_words=contract.maximum_words,
        minimum_take_count=contract.minimum_take_count,
        minimum_semantic_blocks=contract.minimum_semantic_blocks,
        maximum_semantic_blocks=contract.maximum_semantic_blocks,
        contract_json=json.dumps(
            contract.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        contract_hash=contract.contract_hash,
        title=" ".join(str(title or "").split()),
        cta=" ".join(str(cta or "").split()) or "Keine explizite CTA.",
        facts=fact_lines,
        language=" ".join(str(language or "Deutsch").split()),
        actor_context=(
            " ".join(str(actor_context or "").split()) or "Keine zusätzliche Vorgabe."
        ),
    )


def _strip_response_wrappers(raw_text: Any) -> str:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    text = _RESPONSE_LABEL.sub("", text, count=1).strip()
    return " ".join(text.split())


def _normalized_sentences(script: str) -> list[str]:
    return [
        re.sub(r"[^\wÄÖÜäöüß]+", " ", sentence, flags=re.UNICODE)
        .strip()
        .casefold()
        for sentence in _SENTENCE_SPLIT.split(script)
        if sentence.strip()
    ]


def validate_semantic_script(
    script: str,
    *,
    requested_duration_seconds: int,
    maximum_seconds: Optional[int] = None,
) -> SemanticDurationContract:
    """Validate copy against the same duration and semantic-beat contract used to render it."""
    contract = build_semantic_duration_contract(
        requested_duration_seconds,
        maximum_seconds=maximum_seconds,
    )
    cleaned = " ".join(str(script or "").split())
    word_count = script_word_count(cleaned)
    if not contract.minimum_words <= word_count <= contract.maximum_words:
        raise ValueError(
            "Semantic UGC script must fit the canonical word envelope "
            f"{contract.minimum_words}-{contract.maximum_words}; got {word_count}."
        )

    sentences = _normalized_sentences(cleaned)
    if len(sentences) != len(set(sentences)):
        raise ValueError("Semantic UGC script must contain distinct sentences without padding.")

    beats = plan_editorial_beats(cleaned)
    if not contract.minimum_take_count <= len(beats) <= contract.minimum_take_count + 1:
        raise ValueError(
            "Semantic UGC script must plan to the contract minimum take count, "
            "with at most one semantic-boundary exception."
        )
    return contract


_FALLBACK_ADJECTIVES: Sequence[str] = (
    "wichtige",
    "praktische",
    "konkrete",
    "hilfreiche",
    "verlässliche",
    "entscheidende",
    "klare",
    "alltagstaugliche",
)
_FALLBACK_CONTEXT: Sequence[Sequence[str]] = (
    ("hilft", "dir", "bei", "einer", "klaren", "Vorbereitung", "heute"),
    ("zählt", "für", "deinen", "nächsten", "sicheren", "Schritt", "besonders"),
    ("macht", "deine", "nächste", "Entscheidung", "klarer", "und", "leichter"),
    ("schafft", "mehr", "Überblick", "für", "deinen", "konkreten", "Alltag"),
    ("gibt", "deiner", "Planung", "eine", "verlässliche", "praktische", "Richtung"),
    ("verbindet", "gute", "Vorbereitung", "mit", "mehr", "persönlicher", "Sicherheit"),
    ("zeigt", "dir", "konkret", "den", "nächsten", "sinnvollen", "Schritt"),
    ("unterstützt", "dich", "beim", "informierten", "selbstbestimmten", "Weiterplanen", "heute"),
)


def _fallback_prefix(index: int, word_count: int) -> list[str]:
    adjective = _FALLBACK_ADJECTIVES[index]
    if word_count == 5:
        return ["Wichtig", "ist", "dieser", adjective, "Punkt"]
    if word_count == 6:
        return ["Beachte", "heute", "diesen", adjective, "Punkt", "genau"]
    if word_count == 7:
        return ["Für", "deine", "Planung", "zählt", "dieser", adjective, "Punkt"]
    if word_count == 8:
        return [
            "Für",
            "deine",
            "heutige",
            "Planung",
            "zählt",
            "dieser",
            adjective,
            "Punkt",
        ]
    if word_count == 9:
        return [
            "Für",
            "deine",
            "Planung",
            "im",
            "Alltag",
            "zählt",
            "dieser",
            adjective,
            "Punkt",
        ]
    raise ValueError("Semantic UGC fallback prefix must contain five to nine words.")


def _build_fallback_script(
    *,
    title: str,
    cta: str,
    facts: tuple[str, ...],
    contract: SemanticDurationContract,
) -> str:
    block_count = contract.minimum_take_count
    base_words, extra_words = divmod(contract.minimum_words, block_count)
    block_word_counts = [
        base_words + (1 if index < extra_words else 0)
        for index in range(block_count)
    ]
    source_text = " ".join((*facts, str(title or ""), str(cta or "")))
    source_words = _WORD_PATTERN.findall(source_text)
    if not source_words:
        source_words = ["Klare", "Vorbereitung", "erleichtert", "deinen", "nächsten", "Schritt"]

    fact_words = _WORD_PATTERN.findall(facts[0]) if facts else source_words
    sentences = []
    for index, target_words in enumerate(block_word_counts):
        full_fact_prefix_words = target_words - len(fact_words)
        if fact_words and 5 <= full_fact_prefix_words <= 9:
            prefix = _fallback_prefix(index, full_fact_prefix_words)
            content = fact_words
        else:
            prefix = _fallback_prefix(index, 5)
            content_word_count = target_words - len(prefix)
            fact_anchor = fact_words[: min(2, len(fact_words))]
            content_pool = [*fact_anchor, *_FALLBACK_CONTEXT[index], *source_words]
            while len(content_pool) < content_word_count:
                content_pool.extend(_FALLBACK_CONTEXT[index])
            content = content_pool[:content_word_count]
        sentence = " ".join([*prefix, *content]).strip() + "."
        if script_word_count(sentence) != target_words:
            raise ValueError("Could not build a contract-safe Semantic UGC fallback block.")
        sentences.append(sentence)

    script = " ".join(sentences)
    validate_semantic_script(
        script,
        requested_duration_seconds=contract.requested_duration_seconds,
        maximum_seconds=contract.maximum_duration_seconds,
    )
    return script


def generate_semantic_script(
    *,
    post_type: str,
    title: str,
    cta: str,
    facts: Optional[Iterable[str]],
    requested_duration_seconds: int,
    llm_client: Optional[Any] = None,
    language: str = "Deutsch",
    actor_context: Optional[str] = None,
    maximum_seconds: Optional[int] = None,
) -> SemanticScriptResult:
    """Generate and validate one dynamic Semantic UGC script."""
    normalized_post_type = _normalize_post_type(post_type)
    fact_values = _normalize_facts(facts)
    contract = build_semantic_duration_contract(
        requested_duration_seconds,
        maximum_seconds=maximum_seconds,
    )
    prompt = build_semantic_script_prompt(
        post_type=normalized_post_type,
        title=title,
        cta=cta,
        facts=fact_values,
        requested_duration_seconds=requested_duration_seconds,
        language=language,
        actor_context=actor_context,
        maximum_seconds=contract.maximum_duration_seconds,
    )

    try:
        client = llm_client or get_llm_client()
        raw_text = client.generate_gemini_text(
            prompt=prompt,
            system_prompt=SEMANTIC_SCRIPT_SYSTEM_PROMPT,
            temperature=0.4,
            thinking_budget=0,
        )
    except Exception as exc:
        script = _build_fallback_script(
            title=title,
            cta=cta,
            facts=fact_values,
            contract=contract,
        )
        return SemanticScriptResult(
            script=script,
            contract_hash=contract.contract_hash,
            provenance={
                "source": "fallback",
                "post_type": normalized_post_type,
                "template": f"semantic_{normalized_post_type}.txt",
                "provider_error_type": type(exc).__name__,
            },
        )

    script = _strip_response_wrappers(raw_text)
    validate_semantic_script(
        script,
        requested_duration_seconds=requested_duration_seconds,
        maximum_seconds=contract.maximum_duration_seconds,
    )
    return SemanticScriptResult(
        script=script,
        contract_hash=contract.contract_hash,
        provenance={
            "source": "gemini",
            "post_type": normalized_post_type,
            "template": f"semantic_{normalized_post_type}.txt",
        },
    )


__all__ = [
    "SemanticScriptResult",
    "build_semantic_script_prompt",
    "generate_semantic_script",
    "validate_semantic_script",
]
