"""
Pure parsing and dossier-normalization helpers for topic generation.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any, Callable, Dict, List, Optional

import yaml
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.features.topics.schemas import DialogScripts, ProductPromptCandidate, ResearchAgentBatch, ResearchDossier
from app.features.topics.topic_validation import (
    MAX_SCRIPT_CHARS_NO_SPACES,
    MIN_SCRIPT_SECONDS,
    MIN_SCRIPT_WORDS,
    _count_german_markers,
    _find_english_markers,
    _script_non_space_char_count,
    _validate_dialog_script_semantics,
    _validate_dialog_script_tier,
    estimate_script_duration_seconds,
    normalize_framework,
    validate_duration,
    validate_german_content,
    validate_round_robin,
    validate_sources_accessible,
    validate_summary,
    validate_unique_ctas,
)

logger = get_logger(__name__)

FOCUS_STOPWORDS = {
    "und", "oder", "mit", "ohne", "für", "fuer", "im", "in", "am", "an", "bei", "von",
    "auf", "der", "die", "das", "den", "dem", "des", "dein", "deine", "deiner", "deinem",
    "du", "ein", "eine", "einer", "eines", "einem", "zum", "zur", "nach", "vor", "zwischen",
    "während", "waehrend", "perspektive", "winkel", "thema", "titel", "lane", "punkt", "frage",
    "fragen", "alltag",
}

_PROMPT3_LABEL_ALIASES = {
    "produkt": {"produkt", "produktname", "product", "product name", "name"},
    "angle": {"angle", "winkel", "produktwinkel", "aufhänger", "aufhaenger"},
    "script": {"script", "skript", "text", "copy", "sprechtext", "voiceover", "voice over", "dialog", "hook", "hook line", "hooktext"},
    "cta": {"cta", "call to action", "call-to-action", "handlungsaufforderung", "schluss", "schlusssatz"},
    "facts": {"fakten", "facts", "fakt", "faktenliste", "stichpunkte", "stutzfakten", "stützfakten", "support facts"},
}


def _normalize_prompt3_label(value: str) -> str:
    return re.sub(r"[^a-z0-9äöüß ]+", " ", str(value or "").lower()).strip()


def _prompt3_alias_match(label: str) -> Optional[str]:
    normalized = _normalize_prompt3_label(label)
    if not normalized:
        return None
    for canonical, aliases in _PROMPT3_LABEL_ALIASES.items():
        for alias in aliases:
            if normalized == alias:
                return canonical
            if normalized.startswith(alias + " "):
                return canonical
    return None


def _prompt3_alias_match_with_alias(label: str) -> tuple[Optional[str], str]:
    normalized = _normalize_prompt3_label(label)
    if not normalized:
        return None, ""
    for canonical, aliases in _PROMPT3_LABEL_ALIASES.items():
        for alias in aliases:
            if normalized == alias:
                return canonical, alias
            if normalized.startswith(alias + " "):
                return canonical, alias
    return None, ""


def _sanitize_json_text(text: str) -> str:
    replacements = {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u201e": '"',
        "\u201f": '"',
        "\u201a": "'",
        "\u201b": "'",
        "\u00ab": '"',
        "\u00bb": '"',
        "\u2039": "'",
        "\u203a": "'",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _parse_json_or_yaml(text: str) -> Any:
    sanitized = _sanitize_json_text(text)

    def _try_extract_json_fragment(candidate: str) -> Any:
        decoder = json.JSONDecoder()
        for index, char in enumerate(candidate):
            if char not in {"{", "["}:
                continue
            try:
                parsed, _end = decoder.raw_decode(candidate[index:])
                return parsed
            except json.JSONDecodeError:
                continue
        return None

    try:
        return json.loads(sanitized)
    except json.JSONDecodeError:
        pass

    extracted_json = _try_extract_json_fragment(sanitized)
    if extracted_json is not None:
        return extracted_json

    try:
        parsed_yaml = yaml.safe_load(sanitized)
    except yaml.YAMLError as exc:
        raise ValidationError(
            message="PROMPT_1 response not JSON",
            details={"error": str(exc), "snippet": sanitized[:200]},
        ) from exc
    if parsed_yaml is None:
        raise ValidationError(message="PROMPT_1 response empty", details={"snippet": sanitized[:200]})
    return parsed_yaml


def _looks_like_topic_list_payload(parsed: Any) -> bool:
    if not isinstance(parsed, list) or not parsed:
        return False
    dict_items = [item for item in parsed if isinstance(item, dict)]
    if not dict_items:
        return False
    topicish_keys = {
        "topic",
        "script",
        "source_summary",
        "angle_options",
        "lane_candidates",
        "facts",
        "risk_notes",
    }
    matching_items = 0
    for item in dict_items:
        if topicish_keys.intersection(item.keys()):
            matching_items += 1
    return matching_items >= max(2, len(dict_items) // 2)


def parse_prompt1_response(
    raw: str,
    profile: Optional[Any] = None,
    *,
    validate_sources_accessible_fn: Callable = validate_sources_accessible,
    validate_duration_fn: Callable = validate_duration,
    validate_summary_fn: Callable = validate_summary,
    validate_german_content_fn: Callable = validate_german_content,
    validate_round_robin_fn: Callable = validate_round_robin,
    validate_unique_ctas_fn: Callable = validate_unique_ctas,
) -> ResearchAgentBatch:
    cleaned = raw.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    parsed = _parse_json_or_yaml(cleaned)
    payload = parsed if isinstance(parsed, dict) else {"items": parsed}

    if "items" in payload and isinstance(payload["items"], list):
        max_seconds = getattr(profile, "prompt1_max_seconds", 6) if profile is not None else 6
        max_chars_no_spaces = getattr(profile, "prompt1_max_chars_no_spaces", MAX_SCRIPT_CHARS_NO_SPACES) if profile is not None else MAX_SCRIPT_CHARS_NO_SPACES
        for item in payload["items"]:
            if not isinstance(item, dict):
                continue
            if "topic" not in item and "title" in item:
                item["topic"] = item["title"]
            item["caption"] = str(item.get("caption") or item.get("source_summary") or "").strip()
            item["framework"] = normalize_framework(item["framework"]) if item.get("framework") else "PAL"
            if "estimated_duration_s" not in item and "script" in item:
                item["estimated_duration_s"] = estimate_script_duration_seconds(item["script"])
            item["tone"] = str(item.get("tone") or "direkt, freundlich, empowernd, du-Form").strip()
            item["disclaimer"] = str(item.get("disclaimer") or "Keine Rechts- oder medizinische Beratung.").strip()
            if not item.get("source_summary") and item.get("caption"):
                item["source_summary"] = item["caption"]
            if not item.get("sources"):
                item["sources"] = []

            script_words = item.get("script", "").split()
            if script_words:
                while script_words and (
                    estimate_script_duration_seconds(" ".join(script_words)) > max_seconds
                    or _script_non_space_char_count(" ".join(script_words)) > max_chars_no_spaces
                ):
                    script_words.pop()
                trimmed_script = " ".join(script_words).strip()
                if not trimmed_script:
                    raise ValidationError(
                        message="PROMPT_1 script empty after trimming",
                        details={"original": item.get("script", "")},
                    )
                if trimmed_script and trimmed_script[-1] not in ".!?":
                    trimmed_script = trimmed_script.rstrip(",;:") + "."
                item["script"] = trimmed_script
                item["estimated_duration_s"] = estimate_script_duration_seconds(trimmed_script)

    try:
        batch = ResearchAgentBatch(**payload)
    except PydanticValidationError as exc:
        raise ValidationError(
            message="PROMPT_1 response invalid",
            details=json.loads(exc.json()),
        ) from exc

    for item in batch.items:
        if item.script.strip() and item.script.strip()[-1] not in ".!?":
            raise ValidationError(
                message="PROMPT_1 response contains incomplete fragment",
                details={"topic": item.topic, "script": item.script},
            )
        if not item.caption.strip():
            raise ValidationError(
                message="PROMPT_1 response missing caption",
                details={"topic": item.topic},
            )
        validate_summary_fn(item)
        validate_german_content_fn(item)
        validate_sources_accessible_fn(item)
    validate_round_robin_fn(batch.items)
    validate_unique_ctas_fn(batch.items)
    return batch


def parse_prompt2_response(raw: str, max_per_category: int = 5) -> DialogScripts:
    max_per_category = max(1, min(5, max_per_category))
    hook_prefixes = (
        # Original families
        "kennst du", "weißt du", "hast du", "brauchst du", "suchst du", "check mal", "schau dir",
        "hier kommt", "das musst", "stell dir", "ich zeig", "lass mich", "die größte", "wenn du",
        "fast alle", "der unangenehme", "die meisten", "was dir", "bevor du", "alles verändert",
        "dieser kleine", "viele verlassen", "alle reden", "die harte", "schon mal erlebt",
        "ich dachte früher", "ich dachte lange", "neulich ist mir", "wusstest du", "manchmal frage ich",
        "ehrlich gesagt", "von außen", "was viele", "kaum jemand", "dieser eine", "niemand sagt",
        "alle denken", "was meinen alltag", "seit ich", "viele meinen", "der moment", "erst wenn",
        "das frustigste", "eine sache",
        # Provokation und Faktenkonflikt
        "das sagt dir", "hoert auf", "hört auf", "sorry, aber", "niemand redet",
        # Identitaet und Zugehoerigkeit
        "als rollstuhlnutzer", "das betrifft dich", "du kennst dieses", "jeder, der",
        "wenn du schon mal",
        # Zahlen und Spezifitaet
        "nur 2 prozent", "nur jede", "eine einzige zahl", "3 sekunden",
        # Neugier und Alltagsfragen
        "wie kommt man", "was passiert, wenn", "wie funktioniert", "was machst du",
        # Fehler und Warnung
        "dieser fehler", "hoer auf mit", "hör auf mit", "mach diesen fehler",
        "3 dinge, die du",
        # Absurditaet und Realitaetscheck
        "was das gesetz", "deutschland 2025", "deutschland 2026", "2022 sollte",
        # Kurveneffekt
        "das betrifft nicht nur", "barrierefreie",
        # POV
        "pov:",
        # Fragen mit Punch
        "warum ist", "wie kann es sein",
        # Kontrast shorthand
        "dein recht", "auf dem papier",
    )

    def normalize_heading(line: str) -> str:
        cleaned = line.strip()
        cleaned = re.sub(r"^#+\s*", "", cleaned)
        cleaned = re.sub(r"^\*+\s*", "", cleaned)
        return cleaned.strip().strip("*").strip().lower()

    def looks_like_script_start(line: str) -> bool:
        return line.strip().lower().startswith(hook_prefixes)

    headers = {
        "problem-agitieren-lösung ads": "problem_agitate_solution",
        "testimonial ads": "testimonial",
        "testimonial-stil ads": "testimonial",
        "transformations-geschichten ads": "transformation",
        "transformation ads": "transformation",
        "beschreibung": "description",
    }
    buckets: Dict[str, List[str]] = {
        "problem_agitate_solution": [],
        "testimonial": [],
        "transformation": [],
    }
    description_text: Optional[str] = None
    current: Optional[str] = None
    current_script_lines: List[str] = []

    for line in raw.splitlines():
        stripped = line.strip()
        key = headers.get(normalize_heading(stripped))
        if key:
            if current and current_script_lines:
                if current == "description":
                    description_text = " ".join(current_script_lines)
                else:
                    buckets[current].append(" ".join(current_script_lines))
                current_script_lines = []
            current = key
            continue
        if not stripped:
            if current and current_script_lines:
                if current == "description":
                    description_text = " ".join(current_script_lines)
                else:
                    buckets[current].append(" ".join(current_script_lines))
                current_script_lines = []
            continue
        if current and current != "description" and current_script_lines and looks_like_script_start(stripped):
            buckets[current].append(" ".join(current_script_lines))
            current_script_lines = [stripped]
            continue
        if current is None:
            raise ValidationError(message="PROMPT_2 output missing headings", details={"line": stripped})
        current_script_lines.append(stripped)

    if current and current_script_lines:
        if current == "description":
            description_text = " ".join(current_script_lines)
        else:
            buckets[current].append(" ".join(current_script_lines))

    for category, scripts in buckets.items():
        if len(scripts) > max_per_category:
            logger.warning(
                "dialog_scripts_truncated",
                category=category,
                original_count=len(scripts),
                truncated_to=max_per_category,
            )
            buckets[category] = scripts[:max_per_category]

    if buckets["problem_agitate_solution"] and not buckets["testimonial"] and not buckets["transformation"]:
        fallback_script = buckets["problem_agitate_solution"][0]
        buckets["testimonial"] = [fallback_script]
        buckets["transformation"] = [fallback_script]
        logger.info("single_category_format_detected", message="Using Problem-Agitieren-Lösung script as fallback for other categories")

    if buckets["problem_agitate_solution"]:
        fallback_desc = buckets["problem_agitate_solution"][0]
        if not description_text:
            description_text = fallback_desc
            logger.warning("description_synthesized_from_script", message="Beschreibung section missing, using script as fallback description")
        elif len(description_text.strip()) < 35:
            description_text = fallback_desc
            logger.warning("description_replaced_from_script", message="Beschreibung too short, using script as fallback description")

    if description_text and len(description_text.strip()) < 35:
        padded = f"{description_text.strip()} Damit ist das Thema fuer den Alltag klarer einzuordnen."
        description_text = padded.strip()

    payload = {**buckets, "description": description_text}
    try:
        return DialogScripts(**payload)
    except PydanticValidationError as exc:
        raise ValidationError(
            message="PROMPT_2 response invalid",
            details=json.loads(exc.json()),
        ) from exc


def parse_prompt3_response(
    raw: str,
    *,
    fallback_product_name: Optional[str] = None,
    fallback_facts: Optional[List[str]] = None,
) -> ProductPromptCandidate:
    fields: Dict[str, str] = {}
    facts: List[str] = []
    body_lines: List[str] = []
    in_facts = False
    current_section: Optional[str] = None
    section_lines: Dict[str, List[str]] = {
        "produkt": [],
        "angle": [],
        "script": [],
        "cta": [],
    }

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        candidate_key, candidate_alias = _prompt3_alias_match_with_alias(stripped.split(":", 1)[0])
        if candidate_key == "facts" or stripped.lower().startswith(("fakten:", "facts:")):
            in_facts = True
            current_section = "facts"
            continue
        if in_facts and stripped.startswith("-"):
            fact = stripped[1:].strip()
            if fact:
                facts.append(fact)
            continue
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            normalized_key = _prompt3_alias_match(key)
            if normalized_key in {"produkt", "angle", "script", "cta"}:
                value = value.strip()
                if value:
                    fields[normalized_key] = value
                current_section = normalized_key
                in_facts = False
                continue
        if candidate_key in {"produkt", "angle", "script", "cta"}:
            remainder = stripped[len(candidate_alias):].lstrip(" :-")
            if remainder:
                fields[candidate_key] = remainder.strip()
                current_section = candidate_key
                in_facts = False
                continue
            current_section = candidate_key
            in_facts = False
            continue
        if current_section in section_lines:
            section_lines[current_section].append(stripped)
            continue
        if in_facts:
            facts.append(stripped)
        else:
            body_lines.append(stripped)

    if fallback_product_name and "produkt" not in fields:
        fields["produkt"] = fallback_product_name
    if fallback_facts and not facts:
        facts = [fact for fact in fallback_facts if str(fact or "").strip()][:5]

    for section_name, lines in section_lines.items():
        if lines and not fields.get(section_name):
            fields[section_name] = " ".join(lines).strip()

    if "script" not in fields and body_lines:
        fields["script"] = " ".join(body_lines).strip()
    if "angle" not in fields and fields.get("script"):
        first_sentence = re.split(r"(?<=[.!?])\s+", fields["script"], maxsplit=1)[0].strip()
        fields["angle"] = first_sentence[:120] or fields["script"][:120]
    if "cta" not in fields and fields.get("script"):
        cta_source = re.split(r"(?<=[.!?])\s+", fields["script"].strip())[-1].strip()
        fields["cta"] = cta_source[:120] or f"Frag nach {fields.get('produkt', fallback_product_name or '')}".strip()

    if not {"produkt", "angle", "script", "cta"}.issubset(fields):
        raise ValidationError(
            message="PROMPT_3 output missing required fields",
            details={"fields": sorted(fields.keys())},
        )

    try:
        candidate = ProductPromptCandidate(
            product_name=fields["produkt"],
            angle=fields["angle"],
            script=fields["script"],
            cta=fields["cta"],
            facts=[fact for fact in facts if fact][:5],
            estimated_duration_s=estimate_script_duration_seconds(fields["script"]),
        )
    except PydanticValidationError as exc:
        raise ValidationError(
            message="PROMPT_3 output invalid",
            details=json.loads(exc.json()),
        ) from exc
    if not candidate.facts:
        raise ValidationError(
            message="PROMPT_3 output missing facts",
            details={"product_name": candidate.product_name},
        )
    return candidate


def _normalize_list_payload(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _clip_text(value: Any, max_length: int, *, default: str = "") -> str:
    text = str(value or default).strip()
    if len(text) <= max_length:
        return text
    clipped = text[:max_length].rstrip(" ,;:-")
    if clipped.endswith(("und", "oder", "sowie")):
        clipped = clipped.rsplit(" ", 1)[0].rstrip(" ,;:-")
    return clipped or text[:max_length].strip()


def _coerce_priority(value: Any) -> int:
    try:
        priority = int(value)
    except (TypeError, ValueError):
        priority = 10
    return max(1, min(priority, 20))


def _normalize_text_signature(value: Any, *, max_words: int = 6) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    words = re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", text.lower())
    if not words:
        return ""
    return " ".join(words[:max_words]).strip()


def _lane_signature(candidate: Dict[str, Any]) -> str:
    return " | ".join(
        part
        for part in (
            _normalize_text_signature(candidate.get("title"), max_words=5),
            _normalize_text_signature(candidate.get("angle"), max_words=6),
            _normalize_text_signature(candidate.get("lane_family"), max_words=4),
        )
        if part
    )


def _lane_is_distinct(candidate: Dict[str, Any], existing: List[Dict[str, Any]]) -> bool:
    signature = _lane_signature(candidate)
    if not signature:
        return False
    from app.features.topics.topic_validation import compute_bigram_jaccard
    for item in existing:
        other_signature = _lane_signature(item)
        if not other_signature:
            continue
        if signature == other_signature:
            return False
        if compute_bigram_jaccard(signature, other_signature) > 0.55:
            return False
    return True


def _clean_focus_tokens(text: Any) -> List[str]:
    cleaned = re.sub(r"^[\s\-–—:]+", "", str(text or "").strip())
    cleaned = re.sub(
        r"^(?:winkel|perspektive|thema|titel|topic|lane)\s*(?:\d+)?\s*[:\-–—]?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[^\wÄÖÜäöüß\s-]", " ", cleaned).strip()
    tokens = [token for token in cleaned.split() if token]
    filtered: List[str] = []
    for token in tokens:
        normalized = token.lower().strip("-")
        if not normalized or normalized in FOCUS_STOPWORDS:
            continue
        if re.fullmatch(r"\d+", normalized):
            continue
        filtered.append(token.strip("-"))
    return filtered or tokens


def _normalize_length_tiers(value: Any) -> List[int]:
    tiers: List[int] = []
    for tier in list(value or []):
        if not str(tier).strip().isdigit():
            continue
        parsed = int(tier)
        if parsed not in {8, 16, 32} or parsed in tiers:
            continue
        tiers.append(parsed)
    return tiers[:3]


def _build_lane_candidate_from_angle(
    *,
    base_payload: Dict[str, Any],
    angle: str,
    index: int,
    lane_family: str,
) -> Dict[str, Any]:
    topic = str(base_payload.get("topic") or base_payload.get("seed_topic") or "Thema").strip()
    angle_text = str(angle or "").strip()
    title_tokens = _clean_focus_tokens(angle_text or topic)
    title = " ".join(title_tokens[:8]).strip()
    if not title:
        title = _clip_text(angle_text or topic, 240)
    if title and topic and topic.lower() not in title.lower() and len(title.split()) <= 6:
        title = f"{title} - {topic}"
    return {
        "lane_key": f"{_clip_text(base_payload.get('cluster_id') or topic, 40).replace(' ', '_').lower()}-{index + 1}",
        "lane_family": _clip_text(lane_family or "sub_angle", 80, default="sub_angle"),
        "title": title[:240],
        "angle": angle_text[:400],
        "priority": _coerce_priority(index + 1),
        "framework_candidates": list(base_payload.get("framework_candidates") or []),
        "source_summary": _clip_text(base_payload.get("source_summary") or base_payload.get("cluster_summary") or topic, 500),
        "facts": list(base_payload.get("facts") or [])[:10],
        "risk_notes": list(base_payload.get("risk_notes") or [])[:5],
        "disclaimer": _clip_text(base_payload.get("disclaimer"), 200, default="Keine individuelle Rechts-, Therapie- oder Medizinberatung."),
        "lane_overlap_warnings": [],
        "suggested_length_tiers": _normalize_length_tiers(base_payload.get("suggested_length_tiers") or [8, 16, 32]),
    }


def _ensure_minimum_lane_candidates(payload: Dict[str, Any], minimum: int = 3) -> List[Dict[str, Any]]:
    existing = [candidate for candidate in list(payload.get("lane_candidates") or []) if isinstance(candidate, dict)]
    normalized: List[Dict[str, Any]] = []
    for candidate in existing:
        prepared = {
            **candidate,
            "lane_key": _clip_text(candidate.get("lane_key"), 80),
            "lane_family": _clip_text(candidate.get("lane_family"), 80, default="value"),
            "title": _clip_text(candidate.get("title"), 240),
            "angle": _clip_text(candidate.get("angle"), 400),
            "priority": _coerce_priority(candidate.get("priority")),
            "framework_candidates": _normalize_list_payload(candidate.get("framework_candidates"))[:4],
            "source_summary": _clip_text(candidate.get("source_summary"), 500),
            "facts": _normalize_list_payload(candidate.get("facts"))[:10],
            "risk_notes": _normalize_list_payload(candidate.get("risk_notes"))[:5],
            "disclaimer": _clip_text(candidate.get("disclaimer"), 200, default="Keine individuelle Rechts-, Therapie- oder Medizinberatung."),
            "lane_overlap_warnings": _normalize_list_payload(candidate.get("lane_overlap_warnings"))[:5],
            "suggested_length_tiers": _normalize_length_tiers(candidate.get("suggested_length_tiers")),
        }
        if _lane_is_distinct(prepared, normalized):
            normalized.append(prepared)

    angle_sources: List[tuple[str, str]] = []
    for angle in list(payload.get("angle_options") or []):
        angle_text = str(angle or "").strip()
        if angle_text:
            angle_sources.append((angle_text, "angle_option"))
    for fact in list(payload.get("facts") or []):
        fact_text = str(fact or "").strip()
        if fact_text:
            angle_sources.append((fact_text, "fact"))
    for risk in list(payload.get("risk_notes") or []):
        risk_text = str(risk or "").strip()
        if risk_text:
            angle_sources.append((risk_text, "risk"))
    if not angle_sources:
        angle_sources.append((str(payload.get("topic") or payload.get("seed_topic") or "Thema").strip(), "topic"))

    seen_signatures = {_lane_signature(candidate) for candidate in normalized if _lane_signature(candidate)}
    for index, (angle_text, source_kind) in enumerate(angle_sources):
        if len(normalized) >= minimum:
            break
        lane_family = {
            "angle_option": "sub_angle",
            "fact": "evidence",
            "risk": "risk",
            "topic": "topic_cluster",
        }.get(source_kind, "sub_angle")
        synthetic = _build_lane_candidate_from_angle(
            base_payload=payload,
            angle=angle_text,
            index=len(normalized) + index,
            lane_family=lane_family,
        )
        signature = _lane_signature(synthetic)
        if not signature or signature in seen_signatures:
            continue
        if not _lane_is_distinct(synthetic, normalized):
            continue
        seen_signatures.add(signature)
        normalized.append(synthetic)

    if len(normalized) < minimum:
        base_topic = str(payload.get("topic") or payload.get("seed_topic") or "Thema").strip()
        while len(normalized) < minimum:
            synthetic = _build_lane_candidate_from_angle(
                base_payload=payload,
                angle=f"{base_topic} - Perspektive {len(normalized) + 1}",
                index=len(normalized),
                lane_family="topic_cluster",
            )
            if _lane_is_distinct(synthetic, normalized):
                normalized.append(synthetic)
            else:
                break
    return normalized


def _normalize_research_dossier_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload or {})
    raw_sources = list(normalized.get("sources") or [])
    raw_source_urls = list(normalized.get("source_urls") or [])

    canonical_sources: List[Dict[str, str]] = []

    def _append_source(title: Any, url: Any) -> None:
        normalized_url = str(url or "").strip()
        if not normalized_url:
            return
        normalized_title = _clip_text(title, 400) or normalized.get("topic") or normalized.get("seed_topic") or "Quelle"
        canonical_sources.append({"title": normalized_title, "url": normalized_url})

    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        _append_source(item.get("title"), item.get("url"))

    for item in raw_source_urls:
        if isinstance(item, dict):
            _append_source(item.get("title"), item.get("url"))
        elif isinstance(item, str):
            _append_source(None, item)

    if not canonical_sources:
        canonical_sources = []

    deduped_sources: List[Dict[str, str]] = []
    seen_urls: set[str] = set()
    for source in canonical_sources:
        url = str(source.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped_sources.append(
            {
                "title": _clip_text(source.get("title"), 400),
                "url": url,
            }
        )

    normalized["sources"] = deduped_sources[:8]
    normalized["source_urls"] = deduped_sources[:8]
    normalized["cluster_id"] = _clip_text(normalized.get("cluster_id"), 120)
    normalized["topic"] = _clip_text(normalized.get("topic"), 240)
    normalized["anchor_topic"] = _clip_text(normalized.get("anchor_topic"), 240, default=normalized["topic"])
    normalized["seed_topic"] = _clip_text(normalized.get("seed_topic"), 240, default=normalized["anchor_topic"])
    normalized["cluster_summary"] = _clip_text(normalized.get("cluster_summary"), 1200)
    normalized["framework_candidates"] = _normalize_list_payload(normalized.get("framework_candidates"))[:4]
    normalized["source_summary"] = _clip_text(normalized.get("source_summary"), 1200)
    normalized["facts"] = _normalize_list_payload(normalized.get("facts"))[:20]
    normalized["angle_options"] = _normalize_list_payload(normalized.get("angle_options"))[:10]
    normalized["risk_notes"] = _normalize_list_payload(normalized.get("risk_notes"))[:10]
    normalized["disclaimer"] = _clip_text(normalized.get("disclaimer"), 240, default="Keine individuelle Rechts-, Therapie- oder Medizinberatung.")
    normalized["lane_candidates"] = _ensure_minimum_lane_candidates(normalized, minimum=3)[:12]
    return normalized


def _slugify_research_label(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "research-cluster"


def _extract_urls_from_text(text: str) -> List[str]:
    urls: List[str] = []
    for _label, url in re.findall(r"\[([^\]]+)\]\((https?://[^\s)>\"]+)\)", text or ""):
        normalized = url.strip().rstrip(".,;)")
        if normalized and normalized not in urls:
            urls.append(normalized)
    for match in re.findall(r"https?://[^\s)>\"]+", text or ""):
        url = match.strip().rstrip(".,;)")
        if url and url not in urls:
            urls.append(url)
    return urls[:8]


def _extract_markdown_headings(text: str) -> List[str]:
    headings: List[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        heading = stripped.lstrip("#").strip()
        heading = re.sub(r"^(?:Research-?Dossier|Forschungsdossier):\s*", "", heading, flags=re.IGNORECASE)
        if heading:
            headings.append(heading)
    return headings


def _extract_bullets(text: str) -> List[str]:
    bullets: List[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("-", "*", "•")):
            candidate = stripped.lstrip("-*•").strip()
            if candidate:
                bullets.append(candidate)
    return bullets


def _extract_paragraphs(text: str) -> List[str]:
    paragraphs: List[str] = []
    for block in re.split(r"\n\s*\n", text or ""):
        candidate = " ".join(line.strip() for line in block.splitlines() if line.strip())
        candidate = re.sub(r"^#+\s*", "", candidate).strip()
        if candidate:
            paragraphs.append(candidate)
    return paragraphs


def _shorten_text(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _synthesize_research_dossier_from_text(
    text: str,
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    headings = _extract_markdown_headings(cleaned)
    bullets = _extract_bullets(cleaned)
    paragraphs = _extract_paragraphs(cleaned)
    urls = _extract_urls_from_text(cleaned)

    topic = headings[0] if headings else ""
    if topic:
        topic = re.sub(r"^(?:Research-?Dossier|Forschungsdossier):\s*", "", topic, flags=re.IGNORECASE).strip()
    if not topic:
        topic = seed_topic or (paragraphs[0] if paragraphs else "Thema")
    topic = _shorten_text(topic, 240)

    if paragraphs:
        cluster_summary = _shorten_text(" ".join(paragraphs[:2]), 900)
    elif bullets:
        cluster_summary = _shorten_text(" ".join(bullets[:4]), 900)
    else:
        cluster_summary = _shorten_text(cleaned, 900)
    if not cluster_summary:
        cluster_summary = topic

    facts = [item for item in bullets[:12] if len(item) >= 8]
    if not facts and paragraphs:
        facts = [p for p in paragraphs[1:4] if len(p) >= 8]
    if not facts:
        facts = [cluster_summary]

    angle_options: List[str] = []
    for candidate in headings[1:] + bullets:
        short = _shorten_text(candidate, 120)
        if short and short not in angle_options:
            angle_options.append(short)
        if len(angle_options) >= 8:
            break
    if not angle_options:
        angle_options = [topic]

    risk_notes: List[str] = []
    risk_keywords = ("hürde", "risiko", "verzög", "problem", "kosten", "komplex", "ausfall", "abhängig")
    for candidate in bullets + paragraphs:
        lowered = candidate.lower()
        if any(keyword in lowered for keyword in risk_keywords):
            short = _shorten_text(candidate, 160)
            if short not in risk_notes:
                risk_notes.append(short)
        if len(risk_notes) >= 5:
            break
    if not risk_notes:
        risk_notes = [cluster_summary[:160]] if cluster_summary else [topic]

    source_summary = _shorten_text(" ".join(paragraphs[:3] or bullets[:5] or [topic]), 1200)
    if len(source_summary) < 35:
        source_summary = _shorten_text(cluster_summary, 1200)

    sources: List[Dict[str, str]] = []
    for index, url in enumerate(urls, start=1):
        domain = re.sub(r"^https?://", "", url).split("/")[0]
        sources.append({"title": f"Quelle {index}: {domain}", "url": url})
    if not sources:
        sources.append({"title": topic, "url": f"https://example.com/{_slugify_research_label(topic)}"})

    payload: Dict[str, Any] = {
        "cluster_id": f"{_slugify_research_label(topic)}-{secrets.token_hex(4)}",
        "topic": topic,
        "anchor_topic": topic,
        "seed_topic": seed_topic or topic,
        "cluster_summary": cluster_summary,
        "framework_candidates": ["PAL"],
        "sources": sources,
        "source_urls": list(sources),
        "source_summary": source_summary,
        "facts": facts,
        "angle_options": angle_options,
        "risk_notes": risk_notes,
        "disclaimer": "Keine individuelle Rechts-, Therapie- oder Medizinberatung.",
        "lane_candidates": [],
    }
    payload["lane_candidates"] = _ensure_minimum_lane_candidates(payload, minimum=3)[:12]
    return _normalize_research_dossier_payload(payload)


def _synthesize_research_dossier_from_seed(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
) -> Dict[str, Any]:
    topic = _clip_text(seed_topic or "Thema", 240)
    source_summary = (
        f"{topic} bleibt der zentrale Seed für diesen Lauf. "
        "Die Rohantwort war unbrauchbar, daher wird ein sicherer Seed-Fallback verwendet."
    )
    cluster_summary = (
        f"Safely synthesized research dossier for {topic}. "
        "This fallback keeps persistence stable even when provider output is contaminated."
    )
    payload: Dict[str, Any] = {
        "cluster_id": f"{_slugify_research_label(topic)}-{secrets.token_hex(4)}",
        "topic": topic,
        "anchor_topic": topic,
        "seed_topic": seed_topic or topic,
        "cluster_summary": cluster_summary,
        "framework_candidates": ["PAL"],
        "sources": [{"title": topic, "url": f"https://example.com/{_slugify_research_label(topic)}"}],
        "source_urls": [{"title": topic, "url": f"https://example.com/{_slugify_research_label(topic)}"}],
        "source_summary": source_summary,
        "facts": [topic],
        "angle_options": [topic],
        "risk_notes": [topic],
        "disclaimer": "Keine individuelle Rechts-, Therapie- oder Medizinberatung.",
        "lane_candidates": [
            {
                "lane_key": f"{_slugify_research_label(topic)}-seed-1",
                "lane_family": "topic_cluster",
                "title": topic,
                "angle": topic,
                "priority": 1,
                "framework_candidates": ["PAL"],
                "source_summary": topic,
                "facts": [topic],
                "risk_notes": [topic],
                "disclaimer": "Keine individuelle Rechts-, Therapie- oder Medizinberatung.",
                "lane_overlap_warnings": [],
                "suggested_length_tiers": [8, 16, 32],
            }
        ],
    }
    return _normalize_research_dossier_payload(payload)


def parse_topic_research_response(
    raw: str,
    *,
    seed_topic: str = "",
    post_type: str = "value",
    target_length_tier: int = 8,
) -> ResearchDossier:
    cleaned = raw.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    contaminated_topic_list = False
    try:
        parsed = _parse_json_or_yaml(cleaned)
        contaminated_topic_list = _looks_like_topic_list_payload(parsed)
        if contaminated_topic_list:
            raise ValidationError(
                message="Contaminated research payload",
                details={"reason": "topic_list_payload_detected", "seed_topic": seed_topic, "post_type": post_type},
            )
        payload = parsed if isinstance(parsed, dict) else {}
        payload = _normalize_research_dossier_payload(payload)
    except ValidationError:
        try:
            if contaminated_topic_list:
                payload = _synthesize_research_dossier_from_seed(
                    seed_topic=seed_topic,
                    post_type=post_type,
                    target_length_tier=target_length_tier,
                )
            else:
                payload = _synthesize_research_dossier_from_text(
                    cleaned,
                    seed_topic=seed_topic,
                    post_type=post_type,
                    target_length_tier=target_length_tier,
                )
        except PydanticValidationError:
            payload = _synthesize_research_dossier_from_seed(
                seed_topic=seed_topic,
                post_type=post_type,
                target_length_tier=target_length_tier,
            )
    try:
        return ResearchDossier(**payload)
    except PydanticValidationError as exc:
        synthesized = _synthesize_research_dossier_from_text(
            cleaned,
            seed_topic=seed_topic,
            post_type=post_type,
            target_length_tier=target_length_tier,
        )
        try:
            return ResearchDossier(**synthesized)
        except PydanticValidationError as synthesized_exc:
            raise ValidationError(
                message="PROMPT_1 research dossier invalid",
                details=json.loads(synthesized_exc.json()),
            ) from synthesized_exc


def _coerce_prompt2_payload(payload: Dict[str, Any], scripts_required: int) -> DialogScripts:
    buckets: Dict[str, List[str]] = {
        "problem_agitate_solution": list(payload.get("problem_agitate_solution") or []),
        "testimonial": list(payload.get("testimonial") or []),
        "transformation": list(payload.get("transformation") or []),
    }
    description = payload.get("description")

    for category, scripts in buckets.items():
        if len(scripts) > scripts_required:
            logger.warning(
                "dialog_scripts_truncated",
                category=category,
                original_count=len(scripts),
                truncated_to=scripts_required,
            )
            buckets[category] = scripts[:scripts_required]

    if buckets["problem_agitate_solution"] and not buckets["testimonial"] and not buckets["transformation"]:
        fallback_script = buckets["problem_agitate_solution"][0]
        buckets["testimonial"] = [fallback_script]
        buckets["transformation"] = [fallback_script]
        logger.info("single_category_format_detected", message="Using Problem-Agitieren-Lösung script as fallback for other categories")

    try:
        return DialogScripts(
            problem_agitate_solution=buckets["problem_agitate_solution"],
            testimonial=buckets["testimonial"],
            transformation=buckets["transformation"],
            description=description,
        )
    except PydanticValidationError as exc:
        raise ValidationError(
            message="PROMPT_2 structured response invalid",
            details=json.loads(exc.json()),
        ) from exc


def _validate_dialog_scripts_payload(scripts: DialogScripts, profile: Any, topic: str) -> None:
    for bucket_name in ("problem_agitate_solution", "testimonial", "transformation"):
        bucket_scripts = list(getattr(scripts, bucket_name) or [])
        if not bucket_scripts:
            raise ValidationError(
                message="Dialog script bucket is empty",
                details={"topic": topic, "bucket": bucket_name},
            )
        for index, script in enumerate(bucket_scripts):
            _validate_dialog_script_tier(script, profile, context=f"{topic}:{bucket_name}:{index}")
            _validate_dialog_script_semantics(script, context=f"{topic}:{bucket_name}:{index}")
            if _find_english_markers(script) and _count_german_markers(script) == 0:
                raise ValidationError(
                    message="Dialog script must be fully in German",
                    details={"topic": topic, "bucket": bucket_name, "script": script[:200]},
                )
