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
EXTENDED_CAPTION_MIN_CHARS = 450
EXTENDED_CAPTION_MAX_CHARS = 1000
EXTENDED_CAPTION_KEY = "extended"

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

_SOURCE_LABEL_STOPWORDS = _TITLE_STOPWORDS | {
    "quelle", "quellen", "basierend", "auf", "bericht", "studie", "studien", "analyse",
    "status", "quo", "stand", "april", "quelle1", "quelle2", "quelle3",
    "betrifft", "bleiben", "bleibt", "helfen", "hilft", "kosten", "kostet", "muss",
    "muessen", "müssen", "zeigen", "zeigt", "wirkt", "wirken", "braucht", "brauchen",
    "ist", "sind", "wird", "werden", "genau", "wirklich", "viele", "vielen", "viel",
    "oft", "dann", "schon", "hier", "dort", "spaeter", "später",
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


def _normalize_source_url(url: Any) -> str:
    normalized = str(url or "").strip()
    if not normalized:
        return ""
    if not re.match(r"^https?://", normalized, flags=re.IGNORECASE):
        return ""
    return normalized.rstrip("/")


def _compact_source_label(value: Any) -> str:
    text = _normalize_line_breaks(str(value or "")).strip()
    if not text:
        return ""
    text = re.sub(r"(?i)^quelle\s*\d+\s*:\s*", "", text)
    text = re.sub(r"(?i)^basierend\s+auf\s*:\s*", "", text)
    text = re.sub(r"(?i)^https?://\S+$", "", text)
    text = re.sub(r"(?i)\bvertexaisearch\.cloud\.google\.com\b", "", text)
    tokens = [
        token
        for token in re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", text)
        if token.lower() not in _SOURCE_LABEL_STOPWORDS
    ]
    if not tokens:
        return ""
    label = " ".join(tokens[:4]).strip(" -–—,:;")
    if not label:
        return ""
    if len(label) > 48:
        label = label[:45].rstrip(" -–—,:;") + "..."
    return label


def _collect_caption_source_urls(seed_payload: Optional[Dict[str, Any]]) -> List[str]:
    payload = dict(seed_payload or {})
    candidates: List[str] = []

    source = payload.get("source")
    if isinstance(source, dict):
        candidates.append(source.get("url") or "")
    elif isinstance(source, str):
        candidates.append(source)

    for key in ("source_urls", "sources"):
        for item in list(payload.get(key) or []):
            if isinstance(item, dict):
                candidates.append(item.get("url") or "")
            else:
                candidates.append(item)

    urls: List[str] = []
    seen = set()
    for candidate in candidates:
        normalized = _normalize_source_url(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)
    return urls


def _collect_caption_source_labels(
    seed_payload: Optional[Dict[str, Any]],
    research_facts: Optional[List[str]] = None,
) -> List[str]:
    payload = dict(seed_payload or {})
    facts = sanitize_fact_fragments(list(research_facts or []))
    candidates: List[str] = []

    for key in ("source_summary", "cluster_summary", "topic", "canonical_topic"):
        value = payload.get(key)
        if value:
            candidates.append(value)

    source = payload.get("source")
    if isinstance(source, dict):
        candidates.extend([
            source.get("title") or "",
            source.get("label") or "",
            source.get("summary") or "",
        ])
    elif isinstance(source, str):
        candidates.append(source)

    for key in ("source_urls", "sources"):
        for item in list(payload.get(key) or []):
            if isinstance(item, dict):
                candidates.extend([
                    item.get("title") or "",
                    item.get("label") or "",
                    item.get("summary") or "",
                    item.get("url") or "",
                ])
            else:
                candidates.append(item)

    labels: List[str] = []
    seen = set()
    for index, candidate in enumerate(candidates):
        label = _compact_source_label(candidate)
        if not label or "vertexaisearch" in str(candidate).lower() or label.lower().startswith("quelle"):
            fallback_fact = facts[index] if index < len(facts) else (facts[-1] if facts else "")
            label = _compact_source_label(fallback_fact)
        if not label:
            continue
        normalized = label.lower()
        if normalized not in seen:
            seen.add(normalized)
            labels.append(label)
        if len(labels) >= 3:
            break

    if len(labels) < 3:
        for fact in facts:
            label = _compact_source_label(fact)
            if not label:
                continue
            normalized = label.lower()
            if normalized not in seen:
                seen.add(normalized)
                labels.append(label)
            if len(labels) >= 3:
                break

    return labels[:3]


def _collect_caption_facts(
    seed_payload: Optional[Dict[str, Any]], research_facts: Optional[List[str]] = None,
) -> List[str]:
    payload = dict(seed_payload or {})
    strict_seed = payload.get("strict_seed") or {}
    source_facts = list(strict_seed.get("facts") or [])
    if not source_facts:
        source_facts = list(research_facts or [])
    sanitized = sanitize_fact_fragments(source_facts)
    facts: List[str] = []
    seen = set()
    for fact in sanitized:
        cleaned = _clean_caption_fact(fact)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            facts.append(cleaned)
    return facts


def select_caption_profile(seed_payload: Optional[Dict[str, Any]]) -> str:
    facts = _collect_caption_facts(seed_payload)
    urls = _collect_caption_source_urls(seed_payload)
    if len(facts) >= 5 and len(urls) >= 3:
        return EXTENDED_CAPTION_KEY
    return "standard"


def _caption_depth_reason(seed_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    facts = _collect_caption_facts(seed_payload)
    urls = _collect_caption_source_urls(seed_payload)
    return {
        "usable_fact_count": len(facts),
        "source_url_count": len(urls),
        "thresholds": {"facts": 5, "source_urls": 3},
    }


def _ensure_terminal_punctuation(text: str) -> str:
    normalized = _normalize_line_breaks(text).rstrip()
    if not normalized:
        return ""
    if normalized[-1] in ".!?":
        return normalized
    return f"{normalized}."


def _clip_sentence(text: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", _normalize_line_breaks(text)).strip()
    if len(normalized) <= limit:
        return _ensure_terminal_punctuation(normalized)
    clipped = normalized[:limit].rstrip(" ,;:-")
    return _ensure_terminal_punctuation(f"{clipped}...")


def _extended_caption_hashtags(topic_title: str, post_type: str) -> List[str]:
    base = _fallback_caption_hashtags(topic_title, post_type, "curiosity")
    if post_type == "value":
        base.insert(0, "#MehrWissen")
    elif post_type == "product":
        base.insert(0, "#ProduktCheck")
    else:
        base.insert(0, "#MehrKlarheit")
    deduped = list(dict.fromkeys(tag for tag in base if tag))
    return deduped[:3]


def _extended_caption_cta(post_type: str) -> str:
    if post_type == "product":
        return "Speicher dir den Post fuer spaeter und schick ihn weiter."
    return "Speicher dir den Post fuer spaeter und teil ihn weiter."


def _build_extended_caption(
    *,
    topic_title: str,
    post_type: str,
    script: str,
    seed_payload: Optional[Dict[str, Any]],
    research_facts: Optional[List[str]],
) -> Optional[Dict[str, Any]]:
    facts = _collect_caption_facts(seed_payload, research_facts)
    source_urls = _collect_caption_source_urls(seed_payload)
    source_labels = _collect_caption_source_labels(seed_payload, research_facts)
    if len(facts) < 5 or len(source_urls) < 3:
        return None
    if len(source_labels) < 3:
        return None

    headline_fact = _clip_sentence(facts[0], 70)
    why_it_matters = _clip_sentence(
        f"Warum das wichtig ist: {facts[1] if len(facts) > 1 else facts[0]}",
        160,
    )
    what_it_changes = _clip_sentence(
        f"Was das konkret bedeutet: {facts[2] if len(facts) > 2 else facts[1] if len(facts) > 1 else facts[0]}",
        180,
    )
    evidence_lines = [
        f"- {_clip_sentence(fact, 70)}"
        for fact in facts[3:6]
        if fact
    ]
    if len(evidence_lines) < 2:
        return None

    hook = _clip_sentence(f"Wichtiger Punkt: {headline_fact}", 100)
    quick_takeaway = _clip_sentence(
        "Kurz gesagt: " + " ".join(part for part in [why_it_matters, what_it_changes] if part),
        260,
    )
    sources_block = "Basierend auf: " + " · ".join(source_labels[:3])
    hashtags = " ".join(_extended_caption_hashtags(topic_title, post_type))
    body = "\n\n".join([
        hook,
        quick_takeaway,
        "Mehr dazu.\n" + "\n".join(evidence_lines),
        sources_block,
        _extended_caption_cta(post_type),
        hashtags,
    ]).strip()
    return {
        "key": EXTENDED_CAPTION_KEY,
        "body": _normalize_line_breaks(body),
        "facts": facts,
        "source_urls": source_urls,
        "source_labels": source_labels,
    }


def _validate_extended_caption(
    caption: str, *, script: str, source_urls: List[str], source_labels: List[str], fact_count: int,
) -> Dict[str, Any]:
    normalized = _normalize_line_breaks(caption)
    char_count = len(normalized)
    hashtags = _extract_hashtags(normalized)
    if char_count < EXTENDED_CAPTION_MIN_CHARS or char_count > EXTENDED_CAPTION_MAX_CHARS:
        raise ValidationError(
            message="Extended caption does not match target length bucket",
            details={"char_count": char_count, "expected": {"min": EXTENDED_CAPTION_MIN_CHARS, "max": EXTENDED_CAPTION_MAX_CHARS}},
        )
    if "Kurz gesagt:" not in normalized:
        raise ValidationError(message="Extended caption missing summary block", details={})
    if "Basierend auf" not in normalized:
        raise ValidationError(message="Extended caption missing source-label block", details={})
    if len(source_urls) < 3:
        raise ValidationError(message="Extended caption source links too thin", details={"source_urls": source_urls})
    if len(source_labels) < 3:
        raise ValidationError(message="Extended caption source labels too thin", details={"source_labels": source_labels})
    if fact_count < 5:
        raise ValidationError(message="Extended caption fact pool too thin", details={"fact_count": fact_count})
    if len(hashtags) < 3 or len(hashtags) > 6:
        raise ValidationError(message="Extended caption hashtag count invalid", details={"hashtags": hashtags})
    if _count_emojis(normalized) > 1:
        raise ValidationError(message="Extended caption emoji count invalid", details={"emoji_count": _count_emojis(normalized)})
    if _looks_mixed_language(normalized):
        raise ValidationError(message="Extended caption appears mixed-language", details={})
    if _script_overlap_ratio(script, normalized) > 0.85:
        raise ValidationError(message="Extended caption repeats script too closely", details={})
    if re.search(r"https?://", normalized, flags=re.IGNORECASE):
        raise ValidationError(message="Extended caption must not expose raw URLs", details={})
    metadata_issues = detect_metadata_copy_issues(normalized)
    if metadata_issues:
        raise ValidationError(
            message="Extended caption contains research-note leakage or malformed copy",
            details={"issues": metadata_issues},
        )
    return {
        "key": EXTENDED_CAPTION_KEY,
        "body": normalized,
        "char_count": char_count,
        "hashtags": hashtags,
    }


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


def _generate_standard_caption_bundle(
    *,
    canonical_topic: str,
    post_type: str,
    script: str,
    selected_key: str,
    facts: List[str],
    llm_factory: Optional[Callable],
) -> Dict[str, Any]:
    script_hook = extract_script_hook(script)
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
    fallback = _build_fallback_caption_variants(
        topic_title=canonical_topic,
        post_type=post_type,
        script=script,
        research_facts=facts,
        selected_key=selected_key,
    )
    fallback["last_error"] = getattr(last_error, "message", str(last_error)) if last_error else None
    return fallback


def generate_caption_bundle(
    *,
    topic_title: str,
    post_type: str,
    script: str,
    llm_factory: Optional[Callable] = None,
    canonical_topic: Optional[str] = None,
    research_facts: Optional[List[str]] = None,
    seed_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    canonical_topic = str(canonical_topic or topic_title or "").strip()
    selected_key = select_caption_variant_key(topic_title=canonical_topic, post_type=post_type, script=script)
    facts = sanitize_fact_fragments(list(research_facts or []))
    profile = select_caption_profile(seed_payload)
    depth_reason = _caption_depth_reason(seed_payload)
    source_urls = _collect_caption_source_urls(seed_payload)
    source_labels = _collect_caption_source_labels(seed_payload, facts)

    if profile == EXTENDED_CAPTION_KEY:
        try:
            extended = _build_extended_caption(
                topic_title=canonical_topic,
                post_type=post_type,
                script=script,
                seed_payload=seed_payload,
                research_facts=facts,
            )
            if not extended:
                raise ValidationError(message="Extended caption source data too thin", details=depth_reason)
            validated = _validate_extended_caption(
                extended["body"],
                script=script,
                source_urls=extended["source_urls"],
                source_labels=extended["source_labels"],
                fact_count=len(extended["facts"]),
            )
            logger.info(
                "extended_caption_bundle_generated",
                topic_title=canonical_topic[:60],
                char_count=len(validated["body"]),
                source_url_count=len(extended["source_urls"]),
            )
            return {
                "variants": [validated],
                "selected_key": EXTENDED_CAPTION_KEY,
                "selected_body": validated["body"],
                "selection_reason": "research_depth_gate",
                "last_error": None,
                "caption_profile": EXTENDED_CAPTION_KEY,
                "caption_depth_reason": depth_reason,
                "source_urls": extended["source_urls"][:3],
                "source_labels": extended["source_labels"][:3],
            }
        except Exception as exc:
            logger.warning(
                "extended_caption_fallback_to_standard",
                topic_title=canonical_topic[:60],
                error=getattr(exc, "message", str(exc)),
                details=str(getattr(exc, "details", {}))[:200],
            )
            standard_bundle = _generate_standard_caption_bundle(
                canonical_topic=canonical_topic,
                post_type=post_type,
                script=script,
                selected_key=selected_key,
                facts=facts,
                llm_factory=llm_factory,
            )
            standard_bundle["caption_profile"] = "standard"
            standard_bundle["caption_depth_reason"] = depth_reason
            standard_bundle["source_urls"] = source_urls
            standard_bundle["source_labels"] = source_labels
            standard_bundle["last_error"] = getattr(exc, "message", str(exc))
            return standard_bundle

    standard_bundle = _generate_standard_caption_bundle(
        canonical_topic=canonical_topic,
        post_type=post_type,
        script=script,
        selected_key=selected_key,
        facts=facts,
        llm_factory=llm_factory,
    )
    standard_bundle["caption_profile"] = "standard"
    standard_bundle["caption_depth_reason"] = depth_reason
    standard_bundle["source_urls"] = source_urls
    standard_bundle["source_labels"] = source_labels
    return standard_bundle


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
        seed_payload=payload,
    )
    payload["canonical_topic"] = canonical_topic
    payload["caption_bundle"] = bundle
    payload["description"] = bundle["selected_body"]
    payload["caption"] = bundle["selected_body"]
    return payload
