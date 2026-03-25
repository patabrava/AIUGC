from __future__ import annotations

import hashlib
import json
import re
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
_BULLET_PATTERN = re.compile(r"^•\s+\S")
_NUMBERED_PATTERN = re.compile(r"^\d+\.\s+\S")
_COMMON_ENGLISH_WORDS = {
    "the", "and", "your", "with", "for", "this", "that", "you", "from", "into", "just", "only",
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



def _build_caption_prompt(topic_title: str, post_type: str, script: str, context: str) -> str:
    return dedent(
        f"""
        Erstelle exakt 3 deutsche Caption-Varianten fuer einen UGC-Post fuer Rollstuhlnutzer:innen in Deutschland.

        Thema: {topic_title}
        Post-Typ: {post_type}
        Gesprochenes Skript: {script}
        Kontext: {context}

        Gib ausschliesslich valides JSON zurueck mit dem Feld `variants`.
        Jede Variante braucht exakt die Felder `key` und `body`.
        Die drei keys muessen exakt sein:
        - short_paragraph
        - medium_bullets
        - long_structured

        Strukturregeln:
        - short_paragraph: 140-260 Zeichen, genau 1 Absatz, keine Stichpunkte, keine Nummerierung.
        - medium_bullets: 220-420 Zeichen, Hook-Absatz, dann Leerzeile, dann 2-3 Stichpunkte mit `• `. Wenn die Caption laenger wird, fuege vor den Stichpunkten einen zweiten kurzen Absatz ein.
        - long_structured: 350-700 Zeichen, immer 2 kurze Prosa-Absaetze vor der Liste, dann Leerzeile, dann 3-4 Stichpunkte mit `• ` ODER 2-4 nummerierte Zeilen (`1.`, `2.`), nur wenn eine Reihenfolge sinnvoll ist.
        - Nutze echte Absätze mit echten Zeilenumbrüchen.
        - Kein Textblock ohne Struktur bei medium_bullets oder long_structured.
        - Lange Captions duerfen nie aus einem einzigen grossen Absatz vor der Liste bestehen.
        - Wiederhole das Skript nicht einfach.
        - Schreibe direkt, glaubwürdig, hilfreich, teilbar.
        - Ergänze Kontext, Einordnung oder praktische Hinweise ohne neue Fakten zu erfinden.
        - Jede Variante endet mit 2-4 thematisch passenden Hashtags.
        - Alles vollständig auf Deutsch.
        """
    ).strip()



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
    topic = str(topic_title or "Thema").strip()
    context_sentence = re.sub(r"\s+", " ", str(context or "").strip()).rstrip(" ,;:-")
    context_sentence = _HASHTAG_PATTERN.sub("", context_sentence).strip(" ,;:-")
    short_topic = topic[:70].rstrip(" ,;:-")
    medium_topic = topic[:80].rstrip(" ,;:-")
    if key == "short_paragraph":
        return (
            f"Bei {short_topic} steckt oft mehr dran, als man auf den ersten Blick merkt. "
            f"Wenn du die entscheidenden Details früh sortierst, sparst du dir Stress und unnötige Rückfragen. "
            f"#Barrierefrei #RollstuhlAlltag"
        )
    if key == "medium_bullets":
        medium_context = context_sentence[:90].rstrip(" ,;:-")
        if medium_context and not re.search(r"[.!?]$", medium_context):
            medium_context = f"{medium_context}."
        return (
            f"{medium_topic} wirkt oft simpel, entscheidet aber oft ueber Ruhe oder Stress.\n\n"
            f"{medium_context or 'Ein zweiter kurzer Absatz macht den Nutzen schneller klar.'}\n\n"
            f"• Pruefe die Details lieber vorher als unterwegs unter Druck.\n"
            f"• Halte wichtige Infos griffbereit, damit du schneller reagieren kannst.\n"
            f"• Nutze klare Ablaeufe statt alles spontan loesen zu muessen.\n\n"
            f"#Barrierefrei #Alltagstipps #Rollstuhl"
        )
    max_context_chars = max(0, FAMILY_SPECS["long_structured"]["max_chars"] - 340 - len(topic))
    clipped_context = context_sentence[:max_context_chars].rstrip(" ,;:-")
    if clipped_context and not re.search(r"[.!?]$", clipped_context):
        clipped_context = f"{clipped_context}."
    context_paragraph = clipped_context or "Sortiere die Lage erst sauber, bevor du Entscheidungen triffst."
    return (
        f"Wenn es um {topic} geht, hilft dir kein Bauchgefühl, sondern ein klarer Ablauf.\n\n"
        f"{context_paragraph}\n\n"
        f"1. Sortiere zuerst die wichtigsten Nachweise oder Infos.\n"
        f"2. Prüfe dann, welche Stelle oder Unterstützung wirklich zuständig ist.\n"
        f"3. Plane genug Puffer ein, damit unterwegs nichts unnötig kippt.\n\n"
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



def select_caption_variant_key(*, topic_title: str, post_type: str, script: str) -> str:
    digest = hashlib.sha256(f"{topic_title}|{post_type}|{script}".encode("utf-8")).hexdigest()
    return FAMILY_ORDER[int(digest[:8], 16) % len(FAMILY_ORDER)]



def resolve_selected_caption(seed_data: Dict[str, Any]) -> str:
    bundle = dict(seed_data.get("caption_bundle") or {})
    if bundle.get("selected_body"):
        return str(bundle["selected_body"]).strip()
    description = str(seed_data.get("description") or "").strip()
    if description:
        return description
    caption = str(seed_data.get("caption") or "").strip()
    if caption:
        return caption
    return ""



def generate_caption_bundle(
    *,
    topic_title: str,
    post_type: str,
    script: str,
    context: str,
    llm_factory: Callable = get_llm_client,
) -> Dict[str, Any]:
    try:
        llm = llm_factory()
    except Exception:
        return _synthesize_fallback_bundle(topic_title=topic_title, post_type=post_type, script=script, context=context)
    prompt = _build_caption_prompt(topic_title=topic_title, post_type=post_type, script=script, context=context)
    schema = {
        "type": "object",
        "properties": {
            "variants": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["key", "body"],
                },
                "minItems": 3,
                "maxItems": 3,
            }
        },
        "required": ["variants"],
    }
    last_error: Optional[ValidationError] = None
    for _ in range(2):
        try:
            raw = llm.generate_gemini_json(prompt=prompt, json_schema=schema, max_tokens=1400, temperature=0.8)
            bundle = validate_caption_bundle(raw, script)
            break
        except ValidationError as exc:
            last_error = exc
            prompt = f"{prompt}\n\nFEEDBACK: {exc.message}. Details: {json.dumps(exc.details, ensure_ascii=False)[:800]}"
    else:
        try:
            text_response = llm.generate_gemini_text(
                prompt=prompt + "\n\nFalls JSON scheitert, nutze dieses Markerformat: [short_paragraph], [medium_bullets], [long_structured].",
                max_tokens=1400,
                temperature=0.8,
            )
            bundle = validate_caption_bundle(_parse_text_variants(text_response), script)
        except Exception:
            bundle = _synthesize_fallback_bundle(topic_title=topic_title, post_type=post_type, script=script, context=context)
    selected_key = select_caption_variant_key(topic_title=topic_title, post_type=post_type, script=script)
    selected_body = next(item["body"] for item in bundle["variants"] if item["key"] == selected_key)
    return {
        "variants": bundle["variants"],
        "selected_key": selected_key,
        "selected_body": selected_body,
        "selection_reason": "hash_variant" if bundle.get("selection_reason") != "fallback_hash_variant" else "fallback_hash_variant",
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
    bundle = generate_caption_bundle(
        topic_title=topic_title,
        post_type=post_type,
        script=script,
        context=derived_context,
        llm_factory=llm_factory,
    )
    payload["caption_bundle"] = bundle
    payload["description"] = bundle["selected_body"]
    payload["caption"] = payload.get("caption") or next(
        item["body"] for item in bundle["variants"] if item["key"] == "short_paragraph"
    )
    return payload
