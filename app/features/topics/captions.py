from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import structlog

from app.adapters.llm_client import get_llm_client
from app.core.errors import ValidationError
from app.features.topics.topic_validation import (
    detect_metadata_copy_issues,
    sanitize_fact_fragments,
)

logger = structlog.get_logger(__name__)

VARIANT_KEYS = ("curiosity", "personal", "provocative")
CAPTION_MIN_CHARS = 80
CAPTION_MAX_CHARS = 400

_MARKER_PATTERN = re.compile(r"^\[(curiosity|personal|provocative)\]\s*$", re.IGNORECASE)
_HASHTAG_PATTERN = re.compile(r"(?<!\w)#[A-Za-zÀ-ÿ0-9_]+")
_EMOJI_PATTERN = re.compile(r"[\u2600-\u27BF\U00010000-\U0010ffff]")
_COMMON_ENGLISH_WORDS = {
    "the", "and", "your", "with", "for", "this", "that", "you", "from", "into", "just", "only",
}

_TITLE_STOPWORDS = {
    "und", "oder", "mit", "ohne", "für", "fuer", "im", "in", "am", "an", "bei", "von",
    "auf", "der", "die", "das", "den", "dem", "des", "eine", "einer", "eines", "einem",
    "ein", "einen", "einem", "forschung", "forschungsdossier", "dossier", "barrierefreiheit",
    "öpnv", "opnv", "alltag", "einstieg", "platzvergabe", "fahrgastinformation", "begleitservice",
    "fast", "alle", "nicht", "wenn", "sich", "aber", "auch", "noch", "nur", "kann",
    "wird", "sind", "hat", "dein", "sein", "ihr", "wie", "was", "wer", "dir", "dich",
    "mich", "mir", "ist", "war", "mal", "denn", "dann", "weil", "dass", "dieser",
    "diese", "dieses", "diesem", "jeder", "jede", "jedes", "manchmal", "immer",
    "oft", "sehr", "viel", "mehr", "hier", "dort", "schon", "ganz", "gar",
}


def _normalize_line_breaks(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).strip()


def _split_paragraphs(text: str) -> List[str]:
    normalized = _normalize_line_breaks(text)
    return [block.strip() for block in normalized.split("\n\n") if block.strip()]


def _extract_hashtags(text: str) -> List[str]:
    return _HASHTAG_PATTERN.findall(_normalize_line_breaks(text))


def _count_emojis(text: str) -> int:
    return len(_EMOJI_PATTERN.findall(str(text or "")))


def _script_overlap_ratio(script: str, caption: str) -> float:
    script_words = set(re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", str(script or "").lower()))
    caption_words = set(re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", str(caption or "").lower()))
    if not script_words or not caption_words:
        return 0.0
    return len(script_words & caption_words) / max(len(script_words), 1)


def _looks_mixed_language(text: str) -> bool:
    words = re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", str(text or "").lower())
    if not words:
        return False
    english_hits = sum(1 for word in words if word in _COMMON_ENGLISH_WORDS)
    return english_hits >= 4 and english_hits / max(len(words), 1) > 0.12


def _meaningful_title_tokens(topic_title: str) -> List[str]:
    tokens = re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", str(topic_title or "").lower())
    return [token for token in tokens if len(token) > 2 and token not in _TITLE_STOPWORDS]


def _caption_looks_like_title(topic_title: str, caption: str) -> bool:
    title_tokens = _meaningful_title_tokens(topic_title)
    if not title_tokens:
        return False
    normalized_caption = _normalize_line_breaks(caption).lower()
    if not normalized_caption:
        return False
    first_sentence = re.split(r"[.!?]", normalized_caption, maxsplit=1)[0].strip()
    if not first_sentence:
        return False
    title_phrase = " ".join(title_tokens[:4])
    if title_phrase and title_phrase in first_sentence:
        return True
    caption_tokens = re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", first_sentence)
    overlap = sum(1 for token in caption_tokens if token in title_tokens)
    return overlap >= max(3, len(title_tokens) * 2 // 3)


def _resolve_canonical_topic(*, topic_title: str, payload: Optional[Dict[str, Any]] = None) -> str:
    data = dict(payload or {})
    canonical_topic = str(data.get("canonical_topic") or data.get("canonicalTopic") or "").strip()
    if canonical_topic:
        return canonical_topic
    research_title = str(data.get("research_title") or data.get("researchTitle") or "").strip()
    if research_title:
        return research_title
    return str(topic_title or "").strip()


def extract_script_hook(script: str) -> str:
    """Extract the first sentence of the script as the spoken hook."""
    text = _normalize_line_breaks(script).replace("\n", " ").strip()
    if not text:
        return ""
    match = re.search(r"[.!?]", text)
    if match:
        return text[: match.end()].strip()
    return text


def _load_caption_prompt_template() -> str:
    prompt_path = Path(__file__).with_name("prompt_data") / "captions_prompt.txt"
    return prompt_path.read_text(encoding="utf-8").strip()


def _build_caption_prompt(
    topic_title: str,
    post_type: str,
    script: str,
    script_hook: str,
    research_facts: List[str],
) -> str:
    facts_text = "\n".join(
        f"{i + 1}. {fact}" for i, fact in enumerate(research_facts)
    ) if research_facts else "Keine Recherche-Fakten verfuegbar — nutze das Skript als Quelle."
    return _load_caption_prompt_template().format(
        topic_title=topic_title,
        post_type=post_type,
        script=script,
        script_hook=script_hook,
        research_facts=facts_text,
    )


def _clean_caption_fact(fact: str) -> str:
    cleaned = _normalize_line_breaks(fact)
    if not cleaned:
        return ""
    if re.search(r"\b(?:community|forschung|dossier|source_context|recherche)\b", cleaned, flags=re.IGNORECASE):
        return ""
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(" .")
    if len(cleaned) > 120:
        cleaned = cleaned[:117].rstrip(" ,;:") + "..."
    return cleaned


def _fallback_caption_hashtags(topic_title: str, post_type: str, key: str) -> List[str]:
    title = f"{topic_title} {post_type}".lower()
    if "öpnv" in title or "opnv" in title:
        base = ["#BarriereFreiheit", "#ÖPNV", "#Selbstbestimmt"]
    elif post_type == "product":
        base = ["#BarriereFreiheit", "#Mobilitaet", "#Alltagshilfe"]
    elif "rollstuhl" in title:
        base = ["#RollstuhlAlltag", "#BarriereFreiheit", "#Selbstbestimmt"]
    else:
        base = ["#BarriereFreiheit", "#Alltagshilfe", "#Selbstbestimmt"]

    variant_tags = {
        "curiosity": "#WasVieleNichtWissen",
        "personal": "#DuKennstDas",
        "provocative": "#Klartext",
    }
    tags = list(dict.fromkeys([variant_tags.get(key, ""), *base]))
    return [tag for tag in tags if tag][:5]


def _build_fallback_caption_variants(
    *,
    topic_title: str,
    post_type: str,
    script: str,
    research_facts: List[str],
    selected_key: str,
) -> Dict[str, Any]:
    fact = ""
    for candidate in research_facts:
        fact = _clean_caption_fact(candidate)
        if fact:
            break

    fact_sentence = fact or "Kleine Huerden kosten im Alltag oft mehr Kraft, als man zuerst denkt"
    lead_map = {
        "curiosity": "Viele merken erst spaet, wie schnell kleine Huerden Energie fressen.",
        "personal": "Wenn du unterwegs bist, zaehlt am Ende jede gesparte Kraft.",
        "provocative": "Barrierefreiheit darf nicht erst wichtig werden, wenn es schon unbequem ist.",
    }
    cta_map = {
        "curiosity": "Speicher dir das fuer spaeter.",
        "personal": "Schick das an jemanden, der das kennt.",
        "provocative": "Kommentier, wenn du das auch so siehst.",
    }
    variants: List[Dict[str, Any]] = []
    for key in VARIANT_KEYS:
        body = (
            f"{lead_map[key]} {fact_sentence}. "
            f"{cta_map[key]}\n\n"
            f"{' '.join(_fallback_caption_hashtags(topic_title, post_type, key))}"
        )
        validated = validate_caption_variant(key, body, script, max_overlap=0.85)
        variants.append(validated)

    final_key = selected_key if selected_key in VARIANT_KEYS else VARIANT_KEYS[0]
    selected_variant = next((item for item in variants if item["key"] == final_key), variants[0])
    return {
        "variants": variants,
        "selected_key": selected_variant["key"],
        "selected_body": selected_variant["body"],
        "selection_reason": "local_fallback",
    }


def _parse_text_variants(raw: str) -> Dict[str, Any]:
    current_key: Optional[str] = None
    buffer: List[str] = []
    variants: List[Dict[str, str]] = []
    for line in _normalize_line_breaks(raw).split("\n"):
        marker = _MARKER_PATTERN.match(line.strip())
        if marker:
            if current_key is not None:
                variants.append({"key": current_key, "body": "\n".join(buffer).strip()})
            current_key = marker.group(1)
            buffer = []
            continue
        if current_key is not None:
            buffer.append(line)
    if current_key is not None:
        variants.append({"key": current_key, "body": "\n".join(buffer).strip()})
    return {"variants": variants}


def validate_caption_variant(
    key: str, body: str, script: str, *, max_overlap: float = 0.55,
) -> Dict[str, Any]:
    if key not in VARIANT_KEYS:
        raise ValidationError(message="Unknown caption family", details={"key": key})
    normalized = _normalize_line_breaks(body)
    if not normalized:
        raise ValidationError(message="Caption body is empty", details={"key": key})

    hashtags = _extract_hashtags(normalized)
    char_count = len(normalized)

    if char_count < CAPTION_MIN_CHARS or char_count > CAPTION_MAX_CHARS:
        raise ValidationError(
            message="Caption does not match target length bucket",
            details={"key": key, "char_count": char_count, "expected": {"min": CAPTION_MIN_CHARS, "max": CAPTION_MAX_CHARS}},
        )
    if not hashtags:
        raise ValidationError(message="Caption must contain hashtags", details={"key": key})
    if len(hashtags) > 6:
        raise ValidationError(message="Caption hashtag count invalid", details={"key": key, "hashtags": hashtags})
    if _count_emojis(normalized) > 1:
        raise ValidationError(message="Caption emoji count invalid", details={"key": key, "emoji_count": _count_emojis(normalized)})
    if _looks_mixed_language(normalized):
        raise ValidationError(message="Caption appears mixed-language", details={"key": key})
    if _script_overlap_ratio(script, normalized) > max_overlap:
        raise ValidationError(message="Caption repeats script too closely", details={"key": key})

    metadata_issues = detect_metadata_copy_issues(normalized)
    if metadata_issues:
        raise ValidationError(
            message="Caption contains research-note leakage or malformed copy",
            details={"key": key, "issues": metadata_issues},
        )

    return {
        "key": key,
        "body": normalized,
        "char_count": char_count,
        "hashtags": hashtags,
    }


def validate_caption_bundle(
    bundle: Dict[str, Any], script: str, post_type: str = "", *, has_research_facts: bool = True,
) -> Dict[str, Any]:
    max_overlap = 0.55 if has_research_facts else 0.85
    variants = list(bundle.get("variants") or [])
    by_key = {str(item.get("key") or ""): item for item in variants}
    available = set(by_key.keys()) & set(VARIANT_KEYS)
    if not available:
        raise ValidationError(
            message=f"No usable caption variants in LLM response (need one of: {', '.join(VARIANT_KEYS)})",
            details={"required": list(VARIANT_KEYS), "available_keys": list(by_key.keys())},
        )
    validated = []
    for key in VARIANT_KEYS:
        body = str((by_key.get(key) or {}).get("body") or "").strip()
        if not body:
            continue
        try:
            validated.append(validate_caption_variant(key, body, script, max_overlap=max_overlap))
        except ValidationError:
            continue
    if not validated:
        raise ValidationError(message="Caption bundle has no valid variants", details={"keys": list(by_key.keys())})
    selected_key = str(bundle.get("selected_key") or "").strip()
    if selected_key and selected_key not in VARIANT_KEYS:
        selected_key = ""
    return {
        "variants": validated,
        "selected_key": selected_key,
        "selected_body": str(bundle.get("selected_body") or "").strip(),
        "selection_reason": str(bundle.get("selection_reason") or "").strip(),
    }


def select_caption_variant_key(*, topic_title: str, post_type: str, script: str) -> str:
    digest = hashlib.sha256(f"{topic_title}|{post_type}|{script}".encode("utf-8")).hexdigest()
    return VARIANT_KEYS[int(digest[:8], 16) % len(VARIANT_KEYS)]


def resolve_selected_caption(seed_data: Dict[str, Any]) -> str:
    bundle = dict(seed_data.get("caption_bundle") or {})
    selected_body = str(bundle.get("selected_body") or "").strip()
    if selected_body:
        return selected_body
    caption = str(seed_data.get("caption") or "").strip()
    if caption:
        return caption
    description = str(seed_data.get("description") or "").strip()
    if description:
        return description
    return ""


def _select_best_variant(
    variants: List[Dict[str, Any]], preferred_key: str, topic_title: str,
) -> tuple:
    by_key = {v["key"]: v["body"] for v in variants}
    if preferred_key in by_key and not _caption_looks_like_title(topic_title, by_key[preferred_key]):
        return preferred_key, by_key[preferred_key]
    for key in VARIANT_KEYS:
        if key in by_key and not _caption_looks_like_title(topic_title, by_key[key]):
            if key != preferred_key:
                logger.info(
                    "caption_variant_fallback",
                    preferred_key=preferred_key,
                    actual_key=key,
                    reason="title_echo_or_missing_preferred",
                )
            return key, by_key[key]
    first_key = variants[0]["key"]
    logger.warning("caption_all_variants_echo_title", preferred_key=preferred_key, using=first_key)
    return first_key, by_key[first_key]


def generate_caption_bundle(
    *,
    topic_title: str,
    post_type: str,
    script: str,
    llm_factory: Optional[Callable] = None,
    canonical_topic: Optional[str] = None,
    research_facts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    canonical_topic = str(canonical_topic or topic_title or "").strip()
    selected_key = select_caption_variant_key(topic_title=canonical_topic, post_type=post_type, script=script)
    script_hook = extract_script_hook(script)
    facts = sanitize_fact_fragments(list(research_facts or []))
    llm_factory = llm_factory or get_llm_client
    llm = llm_factory()
    prompt = _build_caption_prompt(
        topic_title=canonical_topic,
        post_type=post_type,
        script=script,
        script_hook=script_hook,
        research_facts=facts,
    )
    last_error: Optional[Any] = None
    for attempt in range(3):
        try:
            raw_text = llm.generate_gemini_text(
                prompt=prompt,
                system_prompt=(
                    "Du bist ein Social-Media-Texter fuer barrierefreie Inhalte. "
                    "Antworte ausschliesslich im Markerformat mit [curiosity], [personal] und [provocative]. "
                    "Keine Erklaerungen, kein JSON, kein Markdown."
                ),
                max_tokens=2500,
                temperature=0.8,
            )
            parsed = _parse_text_variants(raw_text)
            bundle = validate_caption_bundle(parsed, script, post_type=post_type, has_research_facts=bool(facts))
            if not bundle["selected_key"] or bundle["selected_key"] not in VARIANT_KEYS:
                bundle["selected_key"] = selected_key
            final_key, final_body = _select_best_variant(
                bundle["variants"], bundle["selected_key"], canonical_topic,
            )
            bundle["selected_key"] = final_key
            bundle["selected_body"] = final_body
            logger.info(
                "caption_bundle_generated",
                topic_title=canonical_topic[:60],
                selected_key=bundle["selected_key"],
                char_count=len(bundle["selected_body"]),
                source="gemini",
            )
            return {
                "variants": bundle["variants"],
                "selected_key": bundle["selected_key"],
                "selected_body": bundle["selected_body"],
                "selection_reason": "hash_variant",
                "last_error": None,
            }
        except Exception as exc:
            last_error = exc
            if isinstance(exc, ValidationError):
                error_message = exc.message
                error_details = str(exc.details)[:200]
            else:
                error_message = "Caption generation provider failure"
                error_details = str(exc)[:200]
            logger.warning(
                "caption_generation_retry",
                topic_title=canonical_topic[:60],
                attempt=attempt + 1,
                error=error_message,
                details=error_details,
            )
            if isinstance(exc, ValidationError):
                prompt = f"{prompt}\n\nFEEDBACK: {exc.message}. Details: {json.dumps(exc.details, ensure_ascii=False)[:800]}"
            continue
    logger.warning(
        "caption_generation_falling_back",
        topic_title=canonical_topic[:60],
        post_type=post_type,
        selected_key=selected_key,
        last_error=getattr(last_error, "message", str(last_error)) if last_error else None,
    )
    return _build_fallback_caption_variants(
        topic_title=canonical_topic,
        post_type=post_type,
        script=script,
        research_facts=facts,
        selected_key=selected_key,
    )


def attach_caption_bundle(
    seed_payload: Dict[str, Any],
    *,
    topic_title: str,
    post_type: str,
    script_fallback: str = "",
    llm_factory: Callable = get_llm_client,
    canonical_topic: Optional[str] = None,
) -> Dict[str, Any]:
    payload = dict(seed_payload or {})
    script = str(payload.get("dialog_script") or payload.get("script") or script_fallback or "").strip()
    if not script:
        return payload
    strict_seed = payload.get("strict_seed") or {}
    research_facts = sanitize_fact_fragments(list(strict_seed.get("facts") or []))
    canonical_topic = _resolve_canonical_topic(
        topic_title=topic_title,
        payload={**payload, **({"canonical_topic": canonical_topic} if canonical_topic else {})},
    )
    bundle = generate_caption_bundle(
        topic_title=canonical_topic,
        post_type=post_type,
        script=script,
        llm_factory=llm_factory,
        canonical_topic=canonical_topic,
        research_facts=research_facts,
    )
    payload["canonical_topic"] = canonical_topic
    payload["caption_bundle"] = bundle
    payload["description"] = bundle["selected_body"]
    payload["caption"] = bundle["selected_body"]
    return payload
