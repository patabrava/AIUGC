"""Just-in-time script contracts for the Semantic UGC production mode."""

from __future__ import annotations

from copy import deepcopy
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
_COMPLETE_STATEMENT_END = re.compile(r"[.!?](?:[\"'»”’)\]}]+)?$")
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


@dataclass(frozen=True)
class SemanticScriptValidationResult:
    contract: SemanticDurationContract
    word_count: int
    planned_take_count: int
    take_count_exception: Optional[Mapping[str, Any]] = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.contract, name)


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


def _build_result_provenance(
    *,
    source: str,
    post_type: str,
    research_provenance: Optional[Mapping[str, Any]],
    source_urls: Optional[Iterable[str]],
    provider_error_type: Optional[str] = None,
) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "source": source,
        "post_type": post_type,
        "template": f"semantic_{post_type}.txt",
        "research": deepcopy(dict(research_provenance or {})),
        "source_urls": [
            url
            for value in source_urls or ()
            if (url := " ".join(str(value or "").split()))
        ],
    }
    if provider_error_type:
        provenance["provider_error_type"] = provider_error_type
    return provenance


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
    take_count_exception_reason: Optional[str] = None,
) -> SemanticScriptValidationResult:
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
    incomplete_beat_indexes = [
        beat.index
        for beat in beats
        if not _COMPLETE_STATEMENT_END.search(beat.text.strip())
    ]
    if incomplete_beat_indexes:
        raise ValueError(
            "Every Semantic UGC beat must be a complete semantic statement; "
            f"incomplete beat indexes: {incomplete_beat_indexes}."
        )
    planned_take_count = len(beats)
    if not contract.minimum_take_count <= planned_take_count <= contract.minimum_take_count + 1:
        raise ValueError(
            "Semantic UGC script must plan to the contract minimum take count, "
            "with at most one semantic-boundary exception."
        )
    exception_reason = " ".join(str(take_count_exception_reason or "").split())
    take_count_exception = None
    if planned_take_count == contract.minimum_take_count + 1:
        if not exception_reason:
            raise ValueError(
                "One extra Semantic UGC take requires a recorded semantic-boundary exception."
            )
        take_count_exception = {
            "minimum_take_count": contract.minimum_take_count,
            "planned_take_count": planned_take_count,
            "reason": exception_reason,
        }
    elif exception_reason:
        raise ValueError(
            "A take-count exception reason is only valid when one extra take is planned."
        )
    return SemanticScriptValidationResult(
        contract=contract,
        word_count=word_count,
        planned_take_count=planned_take_count,
        take_count_exception=take_count_exception,
    )


_FALLBACK_PREFIXES: Sequence[Sequence[str]] = (
    ("Wichtig", "bleibt"),
    ("Beachte", "außerdem"),
    ("Prüfe", "vorab"),
    ("Für", "dich", "gilt"),
    ("Im", "Alltag", "zählt"),
    ("Merke", "dir"),
    ("Entscheidend", "ist"),
    ("Zum", "Abschluss"),
)
_FALLBACK_ACTIONS: Sequence[tuple[str, str, str]] = (
    ("plane", "Schritte", "früh"),
    ("prüfe", "Pläne", "rechtzeitig"),
    ("kläre", "Termine", "vorab"),
    ("sichere", "Abläufe", "frühzeitig"),
    ("ordne", "Hinweise", "rechtzeitig"),
    ("besprich", "Wege", "vorher"),
    ("kontrolliere", "Kontakte", "früh"),
    ("speichere", "Bedarfe", "direkt"),
)


def _fallback_action_coda(index: int, word_count: int) -> list[str]:
    if word_count <= 0:
        return []
    verb, noun, adverb = _FALLBACK_ACTIONS[index]
    variants = {
        1: [verb],
        2: [verb, adverb],
        3: [verb, noun, adverb],
        4: [verb, "alle", noun, adverb],
        5: [verb, "deine", "nächsten", noun, adverb],
        6: [verb, "deine", "nächsten", noun, "deshalb", adverb],
        7: [verb, "deine", "nächsten", "sicheren", noun, "deshalb", adverb],
        8: [
            verb,
            "deine",
            "nächsten",
            "sicheren",
            noun,
            "deshalb",
            "besonders",
            adverb,
        ],
        9: [
            verb,
            "deine",
            "nächsten",
            "sicheren",
            noun,
            "deshalb",
            adverb,
            "und",
            "bewusst",
        ],
        10: [
            verb,
            "deine",
            "nächsten",
            "sicheren",
            noun,
            "deshalb",
            "jetzt",
            adverb,
            "und",
            "bewusst",
        ],
        11: [
            verb,
            "deine",
            "nächsten",
            "sicheren",
            noun,
            "deshalb",
            "jetzt",
            "besonders",
            adverb,
            "und",
            "bewusst",
        ],
        12: [
            verb,
            "deine",
            "nächsten",
            "sicheren",
            noun,
            "deshalb",
            "jetzt",
            "besonders",
            adverb,
            "und",
            "stets",
            "bewusst",
        ],
        13: [
            verb,
            "deine",
            "nächsten",
            "sicheren",
            noun,
            "deshalb",
            "jetzt",
            "besonders",
            adverb,
            "mit",
            "ruhigem",
            "klarem",
            "Fokus",
        ],
    }
    if word_count not in variants:
        raise ValueError("Semantic UGC fallback coda cannot fit the requested block.")
    return variants[word_count]


def _conditional_fallback_statements(
    words: Sequence[str],
    *,
    maximum_words: int,
) -> list[list[str]]:
    normalized_words = [word.casefold() for word in words]
    then_index = normalized_words.index("dann", 1)
    condition = list(words[1:then_index])
    consequence = list(words[then_index + 1 :])
    if not condition or not consequence:
        raise ValueError("Semantic UGC fallback received an incomplete conditional fact.")

    condition_words = [word.casefold() for word in condition]
    concession: list[str] = []
    if "obwohl" in condition_words:
        concession_index = condition_words.index("obwohl")
        concession = condition[concession_index + 1 :]
        condition = condition[:concession_index]
        if not condition or not concession:
            raise ValueError("Semantic UGC fallback cannot safely split the conditional fact.")

    statements = [["Als", "Bedingung", "gilt,", "dass", *condition]]
    if concession:
        concession[-1] = f"{concession[-1]},"
        statements.append(
            [
                "Obwohl",
                *concession,
                "bleibt",
                "diese",
                "Bedingung",
                "bestehen",
            ]
        )
    consequence[-1] = f"{consequence[-1]},"
    statements.append(
        [
            "Dann",
            *consequence,
            "sofern",
            "diese",
            "Bedingungen",
            "gelten",
        ]
    )
    if any(len(statement) > maximum_words for statement in statements):
        raise ValueError(
            "Semantic UGC fallback cannot preserve the conditional fact in complete takes."
        )
    return statements


def _purpose_fallback_statements(
    words: Sequence[str],
    *,
    marker_index: int,
    maximum_words: int,
) -> list[list[str]]:
    requirement = list(words[:marker_index])
    purpose = list(words[marker_index + 1 :])
    normalized_requirement = {word.casefold() for word in requirement}
    if not requirement or not purpose or "gebucht" not in normalized_requirement:
        raise ValueError("Semantic UGC fallback cannot safely split the purpose clause.")
    purpose[-1] = f"{purpose[-1]},"
    statements = [
        requirement,
        ["Damit", *purpose, "ist", "diese", "Buchung", "nötig"],
    ]
    if any(len(statement) > maximum_words for statement in statements):
        raise ValueError(
            "Semantic UGC fallback cannot preserve the booking fact in complete takes."
        )
    return statements


def _fallback_fact_statements(
    facts: Sequence[str],
    *,
    maximum_words: int,
) -> list[list[str]]:
    statements: list[list[str]] = []
    for fact in facts:
        words = _WORD_PATTERN.findall(fact)
        if not words:
            continue
        if len(words) <= maximum_words:
            statements.append(words)
            continue
        normalized_words = [word.casefold() for word in words]
        if normalized_words[:1] == ["wenn"] and "dann" in normalized_words[1:]:
            statements.extend(
                _conditional_fallback_statements(
                    words,
                    maximum_words=maximum_words,
                )
            )
            continue
        if "damit" in normalized_words:
            statements.extend(
                _purpose_fallback_statements(
                    words,
                    marker_index=normalized_words.index("damit"),
                    maximum_words=maximum_words,
                )
            )
            continue
        raise ValueError(
            "Semantic UGC fallback cannot honestly decompose the supplied fact."
        )
    return statements


def _compose_fallback_sentence(
    *,
    index: int,
    target_words: int,
    statement: Sequence[str],
) -> str:
    remaining_words = target_words - len(statement)
    if remaining_words < 0:
        raise ValueError("Semantic UGC fallback statement exceeds one complete take.")
    prefix = list(_FALLBACK_PREFIXES[index])
    if len(prefix) > remaining_words:
        prefix = []
    coda = _fallback_action_coda(index, remaining_words - len(prefix))
    sentence = ""
    if prefix:
        sentence = f"{' '.join(prefix)}: "
    sentence += " ".join(statement)
    if coda:
        sentence += f"; {' '.join(coda)}"
    sentence += "."
    return sentence


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
    source_values: Sequence[str] = facts or tuple(
        value
        for value in (
            " ".join(str(title or "").split()),
            " ".join(str(cta or "").split()),
        )
        if value
    )
    fact_word_sets = _fallback_fact_statements(
        source_values,
        maximum_words=max(block_word_counts),
    )
    if not fact_word_sets:
        raise ValueError("Semantic UGC fallback requires source evidence.")
    if len(fact_word_sets) > block_count:
        raise ValueError(
            "Semantic UGC fallback cannot preserve every source statement in this duration."
        )

    sentences = []
    normalized_sentences = set()
    for index, target_words in enumerate(block_word_counts):
        candidate_indexes = (
            (index,)
            if index < len(fact_word_sets)
            else tuple(
                (index + offset) % len(fact_word_sets)
                for offset in range(len(fact_word_sets))
            )
        )
        sentence = ""
        for candidate_index in candidate_indexes:
            statement = fact_word_sets[candidate_index]
            if len(statement) > target_words:
                continue
            candidate = _compose_fallback_sentence(
                index=index,
                target_words=target_words,
                statement=statement,
            )
            normalized_candidate = _normalized_sentences(candidate)[0]
            if normalized_candidate not in normalized_sentences:
                sentence = candidate
                normalized_sentences.add(normalized_candidate)
                break
        if not sentence:
            raise ValueError(
                "Semantic UGC fallback cannot repeat or discard source statements."
            )
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
    research_provenance: Optional[Mapping[str, Any]] = None,
    source_urls: Optional[Iterable[str]] = None,
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
            provenance=_build_result_provenance(
                source="fallback",
                post_type=normalized_post_type,
                research_provenance=research_provenance,
                source_urls=source_urls,
                provider_error_type=type(exc).__name__,
            ),
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
        provenance=_build_result_provenance(
            source="gemini",
            post_type=normalized_post_type,
            research_provenance=research_provenance,
            source_urls=source_urls,
        ),
    )


__all__ = [
    "SemanticScriptResult",
    "SemanticScriptValidationResult",
    "build_semantic_script_prompt",
    "generate_semantic_script",
    "validate_semantic_script",
]
