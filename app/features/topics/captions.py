from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from textwrap import dedent
from typing import Any, Callable, Dict, List, Optional

from app.adapters.llm_client import get_llm_client
from app.core.errors import ValidationError

FAMILY_ORDER = ("short_paragraph", "medium_bullets", "long_structured")
FAMILY_SPECS: Dict[str, Dict[str, int]] = {
    "short_paragraph": {"min_chars": 140, "max_chars": 260},
    "medium_bullets": {"min_chars": 220, "max_chars": 420},
    "long_structured": {"min_chars": 350, "max_chars": 700},
}
_MARKER_PATTERN = re.compile(r"^\[(short_paragraph|medium_bullets|long_structured)\]\s*$", re.IGNORECASE)
_HASHTAG_PATTERN = re.compile(r"(?<!\w)#[A-Za-zÀ-ÿ0-9_]+")
_EMOJI_PATTERN = re.compile(r"[\u2600-\u27BF\U00010000-\U0010ffff]")
_BULLET_PATTERN = re.compile(r"^•\s+\S")
_NUMBERED_PATTERN = re.compile(r"^\d+\.\s+\S")
_COMMON_ENGLISH_WORDS = {
    "the", "and", "your", "with", "for", "this", "that", "you", "from", "into", "just", "only",
}

_TITLE_STOPWORDS = {
    "und", "oder", "mit", "ohne", "für", "fuer", "im", "in", "am", "an", "bei", "von",
    "auf", "der", "die", "das", "den", "dem", "des", "eine", "einer", "eines", "einem",
    "ein", "einen", "einem", "forschung", "forschungsdossier", "dossier", "barrierefreiheit",
    "öpnv", "opnv", "alltag", "einstieg", "platzvergabe", "fahrgastinformation", "begleitservice",
}


def _normalize_line_breaks(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    lines = [line.rstrip() for line in normalized.split("\n")]
    return "\n".join(lines).strip()



def _split_paragraphs(text: str) -> List[str]:
    normalized = _normalize_line_breaks(text)
    return [block.strip() for block in normalized.split("\n\n") if block.strip()]



def _bullet_lines(text: str) -> List[str]:
    return [line.strip() for line in _normalize_line_breaks(text).split("\n") if _BULLET_PATTERN.match(line.strip())]



def _numbered_lines(text: str) -> List[str]:
    return [line.strip() for line in _normalize_line_breaks(text).split("\n") if _NUMBERED_PATTERN.match(line.strip())]



def _extract_hashtags(text: str) -> List[str]:
    return _HASHTAG_PATTERN.findall(_normalize_line_breaks(text))


def _count_emojis(text: str) -> int:
    return len(_EMOJI_PATTERN.findall(str(text or "")))


def _paragraph_contains_structured_list(paragraph: str) -> bool:
    for line in paragraph.split("\n"):
        stripped = line.strip()
        if _BULLET_PATTERN.match(stripped) or _NUMBERED_PATTERN.match(stripped):
            return True
    return False


def _index_of_first_structured_paragraph(paragraphs: List[str]) -> int:
    for index, paragraph in enumerate(paragraphs):
        if _paragraph_contains_structured_list(paragraph):
            return index
    return -1



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
    return overlap >= max(2, len(title_tokens) // 2)


def _resolve_canonical_topic(*, topic_title: str, payload: Optional[Dict[str, Any]] = None) -> str:
    data = dict(payload or {})
    canonical_topic = str(data.get("canonical_topic") or data.get("canonicalTopic") or "").strip()
    if canonical_topic:
        return canonical_topic
    research_title = str(data.get("research_title") or data.get("researchTitle") or "").strip()
    if research_title:
        return research_title
    return str(topic_title or "").strip()



def validate_caption_variant(key: str, body: str, script: str) -> Dict[str, Any]:
    if key not in FAMILY_SPECS:
        raise ValidationError(message="Unknown caption family", details={"key": key})
    normalized = _normalize_line_breaks(body)
    if not normalized:
        raise ValidationError(message="Caption body is empty", details={"key": key})
    paragraphs = _split_paragraphs(normalized)
    bullets = _bullet_lines(normalized)
    numbered = _numbered_lines(normalized)
    hashtags = _extract_hashtags(normalized)
    char_count = len(normalized)
    bounds = FAMILY_SPECS[key]

    if char_count < bounds["min_chars"] or char_count > bounds["max_chars"]:
        raise ValidationError(
            message="Caption does not match target length bucket",
            details={"key": key, "char_count": char_count, "expected": bounds},
        )
    if len(hashtags) < 2 or len(hashtags) > 4:
        raise ValidationError(message="Caption hashtag count invalid", details={"key": key, "hashtags": hashtags})
    if _count_emojis(normalized) > 1:
        raise ValidationError(message="Caption emoji count invalid", details={"key": key, "emoji_count": _count_emojis(normalized)})
    if _looks_mixed_language(normalized):
        raise ValidationError(message="Caption appears mixed-language", details={"key": key})
    if _script_overlap_ratio(script, normalized) > 0.72:
        raise ValidationError(message="Caption repeats script too closely", details={"key": key})

    if key == "short_paragraph":
        if len(paragraphs) != 1:
            raise ValidationError(message="short_paragraph must contain exactly one paragraph", details={"paragraphs": paragraphs})
        if bullets or numbered:
            raise ValidationError(message="short_paragraph cannot contain bullets or numbering", details={"key": key})
    elif key == "medium_bullets":
        if len(paragraphs) < 2:
            raise ValidationError(message="medium_bullets must contain a paragraph break", details={"key": key})
        list_index = _index_of_first_structured_paragraph(paragraphs)
        if list_index == -1:
            raise ValidationError(message="medium_bullets must contain a structured bullet paragraph", details={"paragraphs": paragraphs})
        prose_paragraphs = paragraphs[:list_index]
        if not prose_paragraphs:
            raise ValidationError(message="medium_bullets hook must be isolated before bullets", details={"paragraphs": paragraphs})
        if any(_paragraph_contains_structured_list(paragraph) for paragraph in prose_paragraphs):
            raise ValidationError(message="medium_bullets hook paragraphs must stay separate from bullets", details={"paragraphs": paragraphs})
        if char_count >= 320 and len(prose_paragraphs) < 2:
            raise ValidationError(
                message="Long medium_bullets captions must use two prose paragraphs before bullets",
                details={"paragraphs": paragraphs, "char_count": char_count},
            )
        if len(bullets) < 2 or len(bullets) > 3:
            raise ValidationError(message="medium_bullets must contain 2-3 bullets", details={"bullets": bullets})
        if numbered:
            raise ValidationError(message="medium_bullets cannot contain numbering", details={"numbered": numbered})
    elif key == "long_structured":
        if len(paragraphs) < 3:
            raise ValidationError(message="long_structured must contain multiple prose paragraphs before the list", details={"key": key, "paragraphs": paragraphs})
        list_index = _index_of_first_structured_paragraph(paragraphs)
        if list_index == -1:
            raise ValidationError(message="long_structured must contain a structured list paragraph", details={"paragraphs": paragraphs})
        prose_paragraphs = paragraphs[:list_index]
        if len(prose_paragraphs) < 2:
            raise ValidationError(
                message="long_structured must contain at least two prose paragraphs before the list",
                details={"paragraphs": paragraphs},
            )
        if any(_paragraph_contains_structured_list(paragraph) for paragraph in prose_paragraphs):
            raise ValidationError(message="long_structured intro paragraphs must stay separate from the list", details={"paragraphs": paragraphs})
        bullet_ok = 3 <= len(bullets) <= 4 and not numbered
        number_ok = 2 <= len(numbered) <= 4 and not bullets
        if not bullet_ok and not number_ok:
            raise ValidationError(
                message="long_structured must contain 3-4 bullets or 2-4 numbered lines",
                details={"bullets": bullets, "numbered": numbered},
            )

    return {
        "key": key,
        "body": normalized,
        "char_count": char_count,
        "paragraph_count": len(paragraphs),
        "hashtags": hashtags,
    }



def validate_caption_bundle(bundle: Dict[str, Any], script: str) -> Dict[str, Any]:
    variants = list(bundle.get("variants") or [])
    by_key = {str(item.get("key") or ""): item for item in variants}
    if set(by_key.keys()) != set(FAMILY_ORDER):
        raise ValidationError(message="Caption bundle must contain exactly the three expected families", details={"keys": list(by_key.keys())})
    validated = [validate_caption_variant(key, str(by_key[key].get("body") or ""), script) for key in FAMILY_ORDER]
    selected_key = str(bundle.get("selected_key") or "").strip()
    if selected_key and selected_key not in FAMILY_ORDER:
        raise ValidationError(message="Selected caption family invalid", details={"selected_key": selected_key})
    return {
        "variants": validated,
        "selected_key": selected_key,
        "selected_body": str(bundle.get("selected_body") or "").strip(),
        "selection_reason": str(bundle.get("selection_reason") or "").strip(),
    }



def _load_caption_prompt_template() -> str:
    prompt_path = Path(__file__).with_name("prompt_data") / "captions_prompt.txt"
    return prompt_path.read_text(encoding="utf-8").strip()


def _build_caption_prompt(topic_title: str, post_type: str, script: str, context: str) -> str:
    return _load_caption_prompt_template().format(
        topic_title=topic_title,
        post_type=post_type,
        script=script,
        context=context,
    )



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



def _fallback_body(topic_title: str, context: str, key: str) -> str:
    topic_raw = re.sub(r"\s+", " ", str(topic_title or "Thema").strip())
    topic_words = [word for word in re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", topic_raw) if len(word) > 2]
    hook_topic = " ".join(topic_words[:3]).strip() or "das Thema"
    context_raw = re.sub(r"\s+", " ", str(context or "").strip())
    context_words = [word for word in re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", context_raw) if len(word) > 3]
    hook_context = " ".join(context_words[:4]).strip() or hook_topic
    if hook_context.lower() in {"kontext", "thema", "details"}:
        hook_context = ""
    digest = hashlib.sha256(f"{topic_raw}|{context_raw}|{key}".encode("utf-8")).hexdigest()
    opener_index = int(digest[:2], 16) % 3
    if key == "short_paragraph":
        openers = (
            f"Im Alltag rund um {hook_topic} helfen kleine Details oft mehr als große Ansagen ✨ ",
            f"Wer {hook_topic} im Blick behält, merkt schnell, wo es im Alltag hakt 🚦 ",
            f"Schon bei {hook_topic} entscheiden kleine Details oft ueber Ruhe oder Stress ✨ ",
        )
        opener = openers[opener_index]
        return (
            f"{opener}"
            "Wenn du die wichtigsten Punkte vorher sortierst, vermeidest du Stress und reagierst unterwegs ruhiger. "
            f"#Barrierefrei #RollstuhlAlltag"
        )
    if key == "medium_bullets":
        openers = (
            "Kleine Details entscheiden oft darüber, ob der Alltag ruhig bleibt oder kippt 🚦",
            f"Gerade bei {hook_topic} zahlt sich ein klarer Plan schnell aus ✨",
            f"Im Alltag mit {hook_topic} merkt man Reibung oft erst an kleinen Stellen 🚦",
        )
        opening = openers[opener_index]
        short_context = " ".join(context_words[:3]).strip() if context_words else ""
        if short_context.lower() in {"kontext", "thema", "details"}:
            short_context = ""
        return (
            f"{opening}\n\n"
            f"{'Mit einem klaren Ablauf bleibst du ruhiger und verlierst den Überblick nicht.' if not short_context else f'Ein klarer Blick auf {short_context} hilft dir, ruhiger zu bleiben.'}\n\n"
            "• Prüfe zuerst, welche Infos gebraucht werden.\n"
            "• Notiere Kernpunkte kurz, damit Rueckfragen schneller geklaert sind.\n"
            "• Plane Puffer ein, falls etwas unklar bleibt.\n\n"
            f"#Barrierefrei #Alltagstipps #Rollstuhl"
        )
    openers = (
        f"Rund um {hook_topic} kippt es oft an fehlender Vorbereitung 🚀",
        f"Gerade bei {hook_topic} zeigt sich schnell, ob der Ablauf wirklich passt ✨",
        f"Bei {hook_topic} wird oft erst unterwegs klar, wo die Reibung entsteht 🚦",
    )
    opening = openers[opener_index]
    return (
        f"{opening}\n\n"
        f"{'Ein klarer Ablauf gibt dir Orientierung und spart Energie.' if not hook_context else f'Ein klarer Blick auf {hook_context} gibt dir Orientierung und spart Energie.'} "
        "Gerade wenn mehrere Stellen beteiligt sind, spart das Zeit und verhindert doppelte Rueckfragen. "
        "Du bleibst handlungsfaehig, statt jedes Mal bei Null anfangen zu muessen.\n\n"
        "1. Kläre zuerst das Ziel und sammle die relevanten Fakten an einem Ort.\n"
        "2. Prüfe danach Zuständigkeiten, Fristen und praktische Hürden Schritt für Schritt.\n"
        "3. Halte Ergebnisse kurz fest, damit du beim nächsten Kontakt sofort anknüpfen kannst.\n\n"
        f"#Barrierefrei #Selbstbestimmt #RollstuhlAlltag"
    )



def _synthesize_fallback_bundle(topic_title: str, post_type: str, script: str, context: str) -> Dict[str, Any]:
    variants = []
    for key in FAMILY_ORDER:
        variants.append(validate_caption_variant(key, _fallback_body(topic_title, context, key), script))
    selected_key = select_caption_variant_key(topic_title=topic_title, post_type=post_type, script=script)
    selected_body = next(item["body"] for item in variants if item["key"] == selected_key)
    return {
        "variants": variants,
        "selected_key": selected_key,
        "selected_body": selected_body,
        "selection_reason": "fallback_hash_variant",
    }



def _caption_variant_pool(post_type: str) -> tuple:
    normalized = str(post_type or "").strip().lower()
    if normalized == "value":
        return ("medium_bullets", "long_structured")
    if normalized == "lifestyle":
        return ("short_paragraph", "medium_bullets")
    return FAMILY_ORDER


def select_caption_variant_key(*, topic_title: str, post_type: str, script: str) -> str:
    digest = hashlib.sha256(f"{topic_title}|{post_type}|{script}".encode("utf-8")).hexdigest()
    pool = _caption_variant_pool(post_type)
    return pool[int(digest[:8], 16) % len(pool)]



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



def generate_caption_bundle(
    *,
    topic_title: str,
    post_type: str,
    script: str,
    context: str,
    llm_factory: Optional[Callable] = None,
    canonical_topic: Optional[str] = None,
) -> Dict[str, Any]:
    canonical_topic = str(canonical_topic or topic_title or "").strip()
    selected_key = select_caption_variant_key(topic_title=canonical_topic, post_type=post_type, script=script)
    llm_factory = llm_factory or get_llm_client
    try:
        llm = llm_factory()
    except Exception:
        return _synthesize_fallback_bundle(canonical_topic, post_type, script, context)
    prompt = _build_caption_prompt(topic_title=canonical_topic, post_type=post_type, script=script, context=context)
    last_error: Optional[ValidationError] = None
    for _ in range(2):
        try:
            raw_text = llm.generate_gemini_text(prompt=prompt, max_tokens=1400, temperature=0.8)
            parsed = _parse_text_variants(raw_text)
            bundle = validate_caption_bundle(parsed, script)
            preferred_pool = _caption_variant_pool(post_type)
            if not bundle["selected_key"] or bundle["selected_key"] not in preferred_pool:
                bundle["selected_key"] = selected_key
            bundle["selected_body"] = next(
                item["body"] for item in bundle["variants"] if item["key"] == bundle["selected_key"]
            )
            if _caption_looks_like_title(canonical_topic, bundle["selected_body"]):
                raise ValidationError(
                    message="Caption repeats topic title too closely",
                    details={"topic_title": canonical_topic, "selected_key": bundle["selected_key"]},
                )
            break
        except ValidationError as exc:
            last_error = exc
            prompt = f"{prompt}\n\nFEEDBACK: {exc.message}. Details: {json.dumps(exc.details, ensure_ascii=False)[:800]}"
        except Exception as exc:
            last_error = ValidationError(
                message="Caption generation failed",
                details={"reason": type(exc).__name__, "message": str(exc)},
            )
    else:
        bundle = _synthesize_fallback_bundle(canonical_topic, post_type, script, context)
    return {
        "variants": bundle["variants"],
        "selected_key": bundle["selected_key"],
        "selected_body": bundle["selected_body"],
        "selection_reason": bundle.get("selection_reason") or "hash_variant",
        "last_error": {"message": last_error.message, "details": last_error.details} if last_error else None,
    }



def attach_caption_bundle(
    seed_payload: Dict[str, Any],
    *,
    topic_title: str,
    post_type: str,
    script_fallback: str = "",
    context: str = "",
    llm_factory: Callable = get_llm_client,
    canonical_topic: Optional[str] = None,
) -> Dict[str, Any]:
    payload = dict(seed_payload or {})
    if (payload.get("caption_bundle") or {}).get("selected_body"):
        selected = resolve_selected_caption(payload)
        if selected:
            payload["description"] = selected
        return payload
    script = str(payload.get("dialog_script") or payload.get("script") or script_fallback or "").strip()
    if not script:
        return payload
    derived_context = context or str(payload.get("description") or payload.get("caption") or "").strip()
    if not derived_context:
        strict_seed = payload.get("strict_seed") or {}
        facts = list(strict_seed.get("facts") or [])
        derived_context = " ".join(str(item).strip() for item in facts if str(item).strip())
    canonical_topic = _resolve_canonical_topic(
        topic_title=topic_title,
        payload={**payload, **({"canonical_topic": canonical_topic} if canonical_topic else {})},
    )
    bundle = generate_caption_bundle(
        topic_title=canonical_topic,
        post_type=post_type,
        script=script,
        context=derived_context,
        llm_factory=llm_factory,
        canonical_topic=canonical_topic,
    )
    payload["canonical_topic"] = canonical_topic
    payload["caption_bundle"] = bundle
    payload["description"] = bundle["selected_body"]
    payload["caption"] = bundle["selected_body"]
    return payload
