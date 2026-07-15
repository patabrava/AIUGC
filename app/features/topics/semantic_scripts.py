"""Just-in-time script contracts for the Semantic UGC production mode."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import google.auth.exceptions
import httpx
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.adapters.llm_client import get_llm_client
from app.core.errors import ThirdPartyError
from app.core.video_profiles import script_word_count
from app.features.shot_production.duration import (
    SemanticDurationContract,
    build_semantic_duration_contract,
)
from app.features.shot_production.planner import (
    estimate_speech_seconds,
    plan_editorial_beats,
)


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
_EXPECTED_LLM_FALLBACK_ERRORS = (
    ThirdPartyError,
    httpx.HTTPError,
    google.auth.exceptions.TransportError,
    google.auth.exceptions.RefreshError,
)

SEMANTIC_SCRIPT_SYSTEM_PROMPT = """Du schreibst natürlich gesprochene UGC-Skripte auf Basis belegter Fakten.
Gib ausschließlich den finalen Sprechtext aus. Jeder Satz trägt eine neue vollständige Aussage bei.
Halte die angegebene Wortspanne und die semantische Take-Struktur exakt ein."""


@dataclass(frozen=True, init=False)
class SemanticScriptResult:
    script: str
    contract_hash: str
    _provenance: Mapping[str, Any]

    def __init__(
        self,
        *,
        script: str,
        contract_hash: str,
        provenance: Mapping[str, Any],
    ) -> None:
        object.__setattr__(self, "script", script)
        object.__setattr__(self, "contract_hash", contract_hash)
        object.__setattr__(self, "_provenance", deepcopy(dict(provenance)))

    @property
    def provenance(self) -> Mapping[str, Any]:
        return deepcopy(dict(self._provenance))


@dataclass(frozen=True)
class SemanticScriptValidationResult:
    contract: SemanticDurationContract
    word_count: int
    planned_take_count: int
    take_count_exception: Optional[Mapping[str, Any]] = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.contract, name)


@dataclass(frozen=True)
class _FallbackSourceQuote:
    words: tuple[str, ...]
    label: str = ""
    leading_ellipsis: bool = False
    trailing_ellipsis: bool = False


@dataclass(frozen=True)
class _FallbackSourceUnit:
    quotes: tuple[_FallbackSourceQuote, ...]


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


def _build_semantic_repair_prompt(
    *,
    original_prompt: str,
    invalid_script: str,
    validation_error: ValueError,
    contract: SemanticDurationContract,
) -> str:
    return f"""Überarbeite den folgenden Entwurf einmal so, dass er den Vertrag exakt erfüllt.
Gib ausschließlich den finalen deutschen Sprechtext aus.

Vertrag:
- {contract.minimum_words} bis {contract.maximum_words} Wörter
- exakt {contract.minimum_take_count} vollständige, unterschiedliche Sätze als semantische Takes
- schreibe jeden dieser {contract.minimum_take_count} Sätze mit exakt 16 Wörtern; der finale Text hat damit exakt {contract.minimum_take_count * 16} Wörter
- höchstens 18 Wörter und höchstens 7,5 Sekunden geschätzte Sprechzeit pro Take
- jeder Take endet mit Satzzeichen und trägt eine neue vollständige Aussage bei
- verwende ausschließlich die Fakten und die CTA aus dem ursprünglichen Auftrag
- zähle die Wörter intern vor der Ausgabe und erweitere den Entwurf; kürze ihn nicht erneut

Festgestellter Validierungsfehler:
{validation_error}

Ungültiger Entwurf:
{invalid_script}

Ursprünglicher Auftrag:
{original_prompt}"""


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
    unsafe_beats = []
    for beat in beats:
        beat_word_count = script_word_count(beat.text)
        estimated_speech_seconds = estimate_speech_seconds(beat_word_count)
        if beat_word_count > 18 or estimated_speech_seconds > 7.5:
            unsafe_beats.append(
                (beat.index, beat_word_count, estimated_speech_seconds)
            )
    if unsafe_beats:
        raise ValueError(
            "Every Semantic UGC beat must stay within 18 words and 7.5 seconds; "
            f"unsafe beats: {unsafe_beats}."
        )
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
_FALLBACK_SAFE_MARKERS = frozenset(
    {
        "aber",
        "damit",
        "dann",
        "denn",
        "doch",
        "falls",
        "jedoch",
        "obwohl",
        "oder",
        "sondern",
        "und",
        "weil",
        "wenn",
        "während",
    }
)
_FALLBACK_NEGATION_MARKERS = frozenset(
    {
        "kein",
        "keine",
        "keinem",
        "keinen",
        "keiner",
        "keines",
        "nicht",
        "nie",
        "niemals",
        "nirgendwo",
        "ohne",
    }
)
_FALLBACK_EXCERPT_LABEL = "Quellenauszug:"
_FALLBACK_SHORTENED_LABEL = "Gekürzter Quellenauszug:"
_FALLBACK_ANCHOR_STOPWORDS = _FALLBACK_SAFE_MARKERS | frozenset(
    {
        "am",
        "an",
        "auf",
        "aus",
        "bei",
        "das",
        "dem",
        "den",
        "der",
        "des",
        "die",
        "ein",
        "eine",
        "einem",
        "einen",
        "einer",
        "eines",
        "für",
        "im",
        "in",
        "ist",
        "kann",
        "mit",
        "muss",
        "nach",
        "sind",
        "vor",
        "von",
        "war",
        "werden",
        "wird",
        "wurde",
        "zu",
        "zum",
        "zur",
    }
)
_FALLBACK_SOURCE_PLACEHOLDER = "{source}"
_FALLBACK_FACT_AWARE_WRAPPERS: Sequence[tuple[int, Sequence[str]]] = (
    (
        1,
        (
            "Prüfe",
            _FALLBACK_SOURCE_PLACEHOLDER,
            "direkt",
            "an",
            "der",
            "bereitgestellten",
            "vollständigen",
            "Quelle",
            "nach",
        ),
    ),
    (
        1,
        (
            "Vergleiche",
            _FALLBACK_SOURCE_PLACEHOLDER,
            "vorab",
            "sorgfältig",
            "mit",
            "dem",
            "vollständigen",
            "belegten",
            "Quellenmaterial",
        ),
    ),
    (
        1,
        (
            "Nutze",
            _FALLBACK_SOURCE_PLACEHOLDER,
            "nur",
            "mit",
            "seinem",
            "vollständigen",
            "belegten",
            "Kontext",
            "weiter",
        ),
    ),
    (
        4,
        (
            "Halte",
            "dich",
            "bei",
            _FALLBACK_SOURCE_PLACEHOLDER,
            "konsequent",
            "an",
            "die",
            "belegte",
            "Quelle",
        ),
    ),
    (
        1,
        (
            "Bewahre",
            _FALLBACK_SOURCE_PLACEHOLDER,
            "unverändert",
            "für",
            "deine",
            "weitere",
            "sorgfältige",
            "Prüfung",
            "auf",
        ),
    ),
    (
        1,
        (
            "Ordne",
            _FALLBACK_SOURCE_PLACEHOLDER,
            "sorgfältig",
            "in",
            "seinen",
            "ursprünglichen",
            "Quellenzusammenhang",
            "wieder",
            "ein",
        ),
    ),
    (
        1,
        (
            "Kontrolliere",
            _FALLBACK_SOURCE_PLACEHOLDER,
            "nochmals",
            "direkt",
            "am",
            "vollständigen",
            "bereitgestellten",
            "Ausgangstext",
            "sorgfältig",
        ),
    ),
    (
        1,
        (
            "Verwende",
            _FALLBACK_SOURCE_PLACEHOLDER,
            "nur",
            "in",
            "seiner",
            "hier",
            "belegten",
            "Quellenbedeutung",
            "weiter",
        ),
    ),
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
        14: [
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
            "mit",
            "klarem",
            "Fokus",
        ],
        15: [
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
            "mit",
            "ruhigem",
            "klarem",
            "Fokus",
        ],
    }
    if word_count not in variants:
        raise ValueError("Semantic UGC fallback coda cannot fit the requested block.")
    return variants[word_count]


def _fallback_fact_words_and_boundaries(fact: str) -> tuple[list[str], set[int]]:
    matches = list(_WORD_PATTERN.finditer(fact))
    words = [match.group(0) for match in matches]
    boundaries = set()
    for index, match in enumerate(matches[:-1]):
        separator = fact[match.end() : matches[index + 1].start()]
        if re.search(r"[.!?;:,]", separator):
            boundaries.add(index + 1)
    for index, word in enumerate(words[1:], start=1):
        if word.casefold() in _FALLBACK_SAFE_MARKERS:
            boundaries.add(index)
    return words, boundaries


def _partition_fallback_fact(
    words: Sequence[str],
    *,
    boundaries: set[int],
    capacities: Sequence[int],
) -> Optional[list[list[str]]]:
    ordered_boundaries = tuple(sorted(boundaries))

    @lru_cache(maxsize=None)
    def partition(start: int, slot: int) -> Optional[tuple[tuple[str, ...], ...]]:
        if start == len(words):
            return ()
        if slot >= len(capacities):
            return None
        capacity = capacities[slot]
        if len(words) - start <= capacity:
            return (tuple(words[start:]),)
        for end in reversed(ordered_boundaries):
            if not start < end <= start + capacity:
                continue
            remainder = partition(end, slot + 1)
            if remainder is not None:
                return (tuple(words[start:end]), *remainder)
        return None

    result = partition(0, 0)
    return [list(statement) for statement in result] if result is not None else None


def _fallback_source_unit_word_count(unit: _FallbackSourceUnit) -> int:
    return sum(
        script_word_count(quote.label) + len(quote.words)
        for quote in unit.quotes
    ) + max(0, len(unit.quotes) - 1)


def _pack_whole_fallback_facts(
    facts: Sequence[Sequence[str]],
    *,
    block_word_counts: Sequence[int],
) -> Optional[list[_FallbackSourceUnit]]:
    units: list[_FallbackSourceUnit] = []
    current_quotes: list[_FallbackSourceQuote] = []
    for words in facts:
        quote = _FallbackSourceQuote(words=tuple(words))
        candidate = _FallbackSourceUnit(quotes=tuple([*current_quotes, quote]))
        target_index = len(units)
        if (
            target_index < len(block_word_counts)
            and _fallback_source_unit_word_count(candidate)
            <= block_word_counts[target_index]
        ):
            current_quotes.append(quote)
            continue
        if not current_quotes:
            return None
        units.append(_FallbackSourceUnit(quotes=tuple(current_quotes)))
        current_quotes = [quote]
        target_index = len(units)
        if (
            target_index >= len(block_word_counts)
            or _fallback_source_unit_word_count(
                _FallbackSourceUnit(quotes=tuple(current_quotes))
            )
            > block_word_counts[target_index]
        ):
            return None
    if current_quotes:
        units.append(_FallbackSourceUnit(quotes=tuple(current_quotes)))
    return units


def _sequential_fallback_units(
    words: Sequence[str],
    *,
    target_words: Sequence[int],
) -> Optional[list[_FallbackSourceUnit]]:
    offset = 0
    units = []
    label_words = script_word_count(_FALLBACK_EXCERPT_LABEL)
    for target in target_words:
        capacity = target - label_words
        if capacity < 1:
            return None
        end = min(len(words), offset + capacity)
        units.append(
            _FallbackSourceUnit(
                quotes=(
                    _FallbackSourceQuote(
                        words=tuple(words[offset:end]),
                        label=_FALLBACK_EXCERPT_LABEL,
                    ),
                )
            )
        )
        offset = end
        if offset == len(words):
            return units
    return None


def _shortened_fallback_unit(
    words: Sequence[str],
    *,
    target_words: int,
) -> _FallbackSourceUnit:
    protected_indexes = {0, len(words) - 1}
    protected_indexes.update(
        index
        for index, word in enumerate(words)
        if word.casefold()
        in (_FALLBACK_SAFE_MARKERS | _FALLBACK_NEGATION_MARKERS)
    )
    intervals = [[index, index + 1] for index in sorted(protected_indexes)]
    merged_intervals: list[list[int]] = []
    for start, end in intervals:
        if merged_intervals and start - merged_intervals[-1][1] <= 3:
            merged_intervals[-1][1] = end
        else:
            merged_intervals.append([start, end])
    intervals = merged_intervals

    label_words = script_word_count(_FALLBACK_SHORTENED_LABEL)

    def unit_word_count() -> int:
        return sum(label_words + end - start for start, end in intervals) + max(
            0,
            len(intervals) - 1,
        )

    if unit_word_count() > target_words:
        raise ValueError(
            "Semantic UGC fallback cannot retain all source markers in this duration."
        )

    while unit_word_count() < target_words:
        changed = False
        for interval_index, interval in enumerate(intervals):
            next_start = (
                intervals[interval_index + 1][0]
                if interval_index + 1 < len(intervals)
                else len(words)
            )
            if interval[1] < next_start - 1:
                interval[1] += 1
                changed = True
                if unit_word_count() == target_words:
                    break
            previous_end = (
                intervals[interval_index - 1][1]
                if interval_index > 0
                else 0
            )
            if unit_word_count() < target_words and interval[0] > previous_end + 1:
                interval[0] -= 1
                changed = True
                if unit_word_count() == target_words:
                    break
        if not changed:
            break

    quotes = tuple(
        _FallbackSourceQuote(
            words=tuple(words[start:end]),
            label=_FALLBACK_SHORTENED_LABEL,
            leading_ellipsis=start > 0,
            trailing_ellipsis=end < len(words),
        )
        for start, end in intervals
    )
    return _FallbackSourceUnit(quotes=quotes)


def _fallback_fact_statements(
    facts: Sequence[str],
    *,
    block_word_counts: Sequence[int],
) -> list[_FallbackSourceUnit]:
    parsed_facts = [_fallback_fact_words_and_boundaries(fact) for fact in facts]
    for words, _ in parsed_facts:
        if not words:
            raise ValueError("Semantic UGC fallback received source text without words.")
    if len(parsed_facts) > len(block_word_counts):
        packed_units = _pack_whole_fallback_facts(
            [words for words, _ in parsed_facts],
            block_word_counts=block_word_counts,
        )
        if packed_units is not None:
            return packed_units
        raise ValueError(
            "Semantic UGC fallback cannot represent every source fact in this duration."
        )

    statements: list[_FallbackSourceUnit] = []
    for fact_index, (words, boundaries) in enumerate(parsed_facts):
        remaining_facts = len(facts) - fact_index - 1
        available_statement_count = (
            len(block_word_counts) - len(statements) - remaining_facts
        )
        target_words = block_word_counts[len(statements)]
        if len(words) <= target_words:
            statements.append(
                _FallbackSourceUnit(
                    quotes=(_FallbackSourceQuote(words=tuple(words)),)
                )
            )
            continue
        available_targets = block_word_counts[
            len(statements) : len(statements) + available_statement_count
        ]
        partitions = _partition_fallback_fact(
            words,
            boundaries=boundaries,
            capacities=[target - 1 for target in available_targets],
        )
        if partitions is not None:
            statements.extend(
                _FallbackSourceUnit(
                    quotes=(
                        _FallbackSourceQuote(
                            words=tuple(part),
                            label=_FALLBACK_EXCERPT_LABEL,
                        ),
                    )
                )
                for part in partitions
            )
            continue
        sequential_units = _sequential_fallback_units(
            words,
            target_words=available_targets,
        )
        if sequential_units is not None:
            statements.extend(sequential_units)
            continue
        statements.append(
            _shortened_fallback_unit(
                words,
                target_words=target_words,
            )
        )
    return statements


def _fallback_source_words(unit: _FallbackSourceUnit) -> list[str]:
    source_words = [word for quote in unit.quotes for word in quote.words]
    if not source_words:
        raise ValueError("Semantic UGC fallback source unit cannot be empty.")
    return source_words


def _fallback_anchor_word(unit: _FallbackSourceUnit) -> str:
    source_words = _fallback_source_words(unit)
    meaningful_words = [
        (index, word)
        for index, word in enumerate(source_words)
        if word.casefold() not in _FALLBACK_ANCHOR_STOPWORDS
    ]
    if not meaningful_words:
        return source_words[0]
    capitalized_words = [
        item for item in meaningful_words if item[1][:1].isupper()
    ]
    candidates = capitalized_words or meaningful_words
    _, anchor = max(
        candidates,
        key=lambda item: (len(item[1]), -item[0]),
    )
    return anchor


def _compose_fallback_source_sentence(
    *,
    index: int,
    target_words: int,
    statement: _FallbackSourceUnit,
) -> str:
    remaining_words = target_words - _fallback_source_unit_word_count(statement)
    if remaining_words < 0:
        raise ValueError("Semantic UGC fallback statement exceeds one complete take.")
    coda = _fallback_action_coda(index, remaining_words)

    def render_quote(quote: _FallbackSourceQuote) -> str:
        quote_text = " ".join(quote.words)
        if quote.leading_ellipsis:
            quote_text = f"… {quote_text}"
        if quote.trailing_ellipsis:
            quote_text = f"{quote_text} …"
        label = f"{quote.label} " if quote.label else ""
        return f"{label}„{quote_text}“"

    sentence = " und ".join(render_quote(quote) for quote in statement.quotes)
    if coda:
        sentence += f"; {' '.join(coda)}"
    sentence += "."
    return sentence


def _fallback_fact_aware_sentence(
    *,
    wrapper_index: int,
    target_words: int,
    source_statement: _FallbackSourceUnit,
) -> str:
    if not 9 <= target_words <= 16:
        raise ValueError("Semantic UGC fallback wrapper requires 9 to 16 words.")
    if not 0 <= wrapper_index < len(_FALLBACK_FACT_AWARE_WRAPPERS):
        raise ValueError("Semantic UGC fallback exhausted its distinct wrapper bank.")
    insertion_index, base_words = _FALLBACK_FACT_AWARE_WRAPPERS[wrapper_index]
    if len(base_words) != 9:
        raise ValueError("Semantic UGC fallback wrapper must contain nine words.")
    if sum(word == _FALLBACK_SOURCE_PLACEHOLDER for word in base_words) != 1:
        raise ValueError("Semantic UGC fallback wrapper requires one source anchor.")
    modifiers: Sequence[str] = {
        9: (),
        10: ("erneut",),
        11: ("bitte", "erneut"),
        12: ("zur", "Sicherheit", "erneut"),
        13: ("vor", "jeder", "Nutzung", "erneut"),
        14: ("vor", "jeder", "Nutzung", "bitte", "erneut"),
        15: ("für", "deine", "nächste", "Entscheidung", "bitte", "erneut"),
        16: (
            "für",
            "deine",
            "nächste",
            "wichtige",
            "Entscheidung",
            "bitte",
            "erneut",
        ),
    }[target_words]
    anchor = _fallback_anchor_word(source_statement)
    anchored_base_words = [
        f"„{anchor}“" if word == _FALLBACK_SOURCE_PLACEHOLDER else word
        for word in base_words
    ]
    words = [
        *anchored_base_words[:insertion_index],
        *modifiers,
        *anchored_base_words[insertion_index:],
    ]
    return f"{' '.join(words)}."


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
        block_word_counts=block_word_counts,
    )
    if not fact_word_sets:
        raise ValueError("Semantic UGC fallback requires source evidence.")

    sentences = []
    for index, target_words in enumerate(block_word_counts):
        if index < len(fact_word_sets):
            sentence = _compose_fallback_source_sentence(
                index=index,
                target_words=target_words,
                statement=fact_word_sets[index],
            )
        else:
            wrapper_index = index - len(fact_word_sets)
            source_index = wrapper_index % len(fact_word_sets)
            sentence = _fallback_fact_aware_sentence(
                wrapper_index=wrapper_index,
                target_words=target_words,
                source_statement=fact_word_sets[source_index],
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

    client = llm_client or get_llm_client()
    try:
        raw_text = client.generate_gemini_text(
            prompt=prompt,
            system_prompt=SEMANTIC_SCRIPT_SYSTEM_PROMPT,
            temperature=0.4,
            thinking_budget=0,
        )
    except _EXPECTED_LLM_FALLBACK_ERRORS as exc:
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
    source = "gemini"
    try:
        validate_semantic_script(
            script,
            requested_duration_seconds=requested_duration_seconds,
            maximum_seconds=contract.maximum_duration_seconds,
        )
    except ValueError as validation_error:
        repair_prompt = _build_semantic_repair_prompt(
            original_prompt=prompt,
            invalid_script=script,
            validation_error=validation_error,
            contract=contract,
        )
        try:
            repaired_raw_text = client.generate_gemini_text(
                prompt=repair_prompt,
                system_prompt=SEMANTIC_SCRIPT_SYSTEM_PROMPT,
                temperature=0.2,
                thinking_budget=0,
            )
        except _EXPECTED_LLM_FALLBACK_ERRORS as exc:
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
        repaired_script = _strip_response_wrappers(repaired_raw_text)
        try:
            validate_semantic_script(
                repaired_script,
                requested_duration_seconds=requested_duration_seconds,
                maximum_seconds=contract.maximum_duration_seconds,
            )
        except ValueError:
            script = _build_fallback_script(
                title=title,
                cta=cta,
                facts=fact_values,
                contract=contract,
            )
            source = "fallback"
        else:
            script = repaired_script
            source = "gemini_repair"
    return SemanticScriptResult(
        script=script,
        contract_hash=contract.contract_hash,
        provenance=_build_result_provenance(
            source=source,
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
