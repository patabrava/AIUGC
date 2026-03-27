from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from textwrap import dedent
from typing import Any, Callable, Dict, List, Optional

import structlog

from app.adapters.llm_client import get_llm_client
from app.core.errors import ValidationError
from app.features.topics.topic_validation import sanitize_fact_fragments, sanitize_metadata_text

logger = structlog.get_logger(__name__)

FAMILY_ORDER = ("short_paragraph", "medium_bullets", "long_structured")
FAMILY_SPECS: Dict[str, Dict[str, int]] = {
    "short_paragraph": {"min_chars": 80, "max_chars": 500},
    "medium_bullets": {"min_chars": 150, "max_chars": 700},
    "long_structured": {"min_chars": 150, "max_chars": 1200},
}
_MARKER_PATTERN = re.compile(r"^\[(short_paragraph|medium_bullets|long_structured)\]\s*$", re.IGNORECASE)
_HASHTAG_PATTERN = re.compile(r"(?<!\w)#[A-Za-zÀ-ÿ0-9_]+")
_EMOJI_PATTERN = re.compile(r"[\u2600-\u27BF\U00010000-\U0010ffff]")
_BULLET_PATTERN = re.compile(r"^•\s+\S")
_NUMBERED_PATTERN = re.compile(r"^\d+\.\s+\S")
_SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")
_CAPTION_ABBREVIATIONS = (
    (re.compile(r"\bz\.\s*b\.", flags=re.IGNORECASE), "__CAP_ZB__"),
    (re.compile(r"\bu\.\s*a\.", flags=re.IGNORECASE), "__CAP_UA__"),
    (re.compile(r"\bd\.\s*h\.", flags=re.IGNORECASE), "__CAP_DH__"),
    (re.compile(r"\bbzw\.", flags=re.IGNORECASE), "__CAP_BZW__"),
)
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

_FALLBACK_OPENERS = {
    "value": (
        "Viele merken erst spät, wie stark verlässliche Unterstützung im Alltag entlastet.",
        "Oft machen kleine Strukturen den Unterschied, wenn im Alltag viele Dinge gleichzeitig laufen.",
        "Gerade im Alltag zeigt sich schnell, wie wertvoll klare Unterstützung wirklich ist.",
    ),
    "product": (
        "Das richtige Hilfsmittel spart im Alltag nicht nur Zeit, sondern oft auch viel Kraft.",
        "Im Alltag zeigt sich schnell, ob ein Produkt wirklich entlastet oder nur gut klingt.",
        "Praktische Lösungen wirken oft unscheinbar, machen aber im Alltag einen spürbaren Unterschied.",
    ),
    "lifestyle": (
        "Im Alltag helfen oft die kleinen Routinen, damit Belastung nicht still immer größer wird.",
        "Viele Veränderungen wirken erst klein, entlasten aber im Alltag schneller als gedacht.",
        "Gerade dann, wenn vieles gleichzeitig läuft, helfen klare Routinen und gute Hinweise.",
    ),
    "default": (
        "Viele merken erst spät, wie stark gute Vorbereitung im Alltag entlastet.",
        "Oft helfen wenige klare Schritte mehr als noch ein zusätzlicher Kraftakt.",
        "Im Alltag machen kleine Anpassungen oft schneller einen Unterschied als große Versprechen.",
    ),
}

_FALLBACK_SUPPORT_SENTENCES = {
    "value": (
        "Wenn du Bedürfnisse früh ansprichst und Unterstützung konkret organisierst, bleibt mehr Energie für den eigentlichen Alltag.",
        "Klare Absprachen und ein verlässliches Umfeld senken Druck, Rückfragen und unnötige Umwege.",
        "Wer Hinweise, Kontakte und nächste Schritte griffbereit hat, reagiert im Alltag ruhiger und sicherer.",
    ),
    "product": (
        "Wenn du Nutzen und Grenzen früh prüfst, vermeidest du Fehlkäufe und sparst dir später unnötige Korrekturen.",
        "Eine passende Lösung hilft nur dann wirklich, wenn sie in deine Routinen passt und im Alltag verlässlich funktioniert.",
        "Wer Funktionen, Grenzen und Einsatzorte vorher sortiert, trifft im Alltag klarere Entscheidungen.",
    ),
    "lifestyle": (
        "Wenn Routinen, Hinweise und kleine Entlastungen gut zusammenpassen, wird der Alltag sofort planbarer.",
        "Wer kleine Gewohnheiten stabil hält, nimmt Druck aus Situationen, die sonst unnötig Kraft kosten.",
        "Klarheit im Alltag entsteht oft nicht durch Tempo, sondern durch verlässliche kleine Schritte.",
    ),
    "default": (
        "Wer relevante Hinweise früh sortiert, spart im Alltag Rückfragen, Umwege und unnötigen Druck.",
        "Klare kleine Schritte helfen oft mehr als hektische Nachbesserungen unter Zeitdruck.",
        "Wenn gute Hinweise greifbar sind, werden Entscheidungen im Alltag deutlich leichter.",
    ),
}

_FALLBACK_BULLETS = {
    "value": (
        "Sprich konkrete Bedürfnisse früh an, damit Unterstützung nicht vom Zufall abhängt.",
        "Halte wichtige Kontakte, Fristen oder Hinweise griffbereit, damit du im Alltag ruhiger reagieren kannst.",
        "Plane kleine Puffer ein, damit Belastung nicht wächst, sobald etwas ungeplant dazwischenkommt.",
        "Notiere Absprachen kurz und klar, damit Hilfe im Alltag verlässlich und nachvollziehbar bleibt.",
    ),
    "product": (
        "Prüfe vorab, welche Funktion dir im Alltag wirklich Arbeit abnimmt und welche nur gut klingt.",
        "Teste die Lösung möglichst nah an deiner Routine, damit du spätere Umwege oder Nachkäufe vermeidest.",
        "Halte fest, wann das Produkt hilft und wann eine andere Lösung sinnvoller ist.",
        "Plane genug Zeit für Einrichtung und Anpassung ein, damit die Entlastung im Alltag auch wirklich ankommt.",
    ),
    "lifestyle": (
        "Mach hilfreiche Schritte so klein, dass du sie auch an anstrengenden Tagen zuverlässig schaffst.",
        "Lege dir sichtbare Hinweise an, damit gute Routinen nicht im Stress untergehen.",
        "Sprich offen an, was dir gerade hilft, damit Unterstützung nicht erraten werden muss.",
        "Halte kleine Entlastungen fest, damit gute Tage planbarer werden und schlechte Tage weniger Druck haben.",
    ),
    "default": (
        "Halte die wichtigsten Hinweise kurz fest, damit Entscheidungen im Alltag nicht jedes Mal neu beginnen.",
        "Plane kleine sichere Schritte statt großer Sprünge, damit Fortschritt auch unter Druck stabil bleibt.",
        "Sprich Zuständigkeiten und Erwartungen klar aus, damit Unterstützung im Alltag verlässlich bleibt.",
        "Lege dir eine einfache Reihenfolge zurecht, damit du auch in hektischen Momenten handlungsfähig bleibst.",
    ),
}

_FALLBACK_DEFAULT_HASHTAGS = {
    "value": ("#Barrierefrei", "#Alltagstipps", "#Selbstbestimmt"),
    "product": ("#Hilfsmittel", "#Alltag", "#Barrierefrei"),
    "lifestyle": ("#Alltag", "#Selbstbestimmt", "#Routine"),
    "default": ("#Barrierefrei", "#Alltag", "#Teilhabe"),
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


def _split_sentences(text: str) -> List[str]:
    cleaned = _normalize_line_breaks(text).replace("\n", " ").strip()
    if not cleaned:
        return []
    protected = cleaned
    for pattern, placeholder in _CAPTION_ABBREVIATIONS:
        protected = pattern.sub(placeholder, protected)
    sentences: List[str] = []
    for chunk in _SENTENCE_PATTERN.split(protected):
        chunk = (
            chunk.replace("__CAP_ZB__", "z.B.")
            .replace("__CAP_UA__", "u.a.")
            .replace("__CAP_DH__", "d.h.")
            .replace("__CAP_BZW__", "bzw.")
        )
        sentence = sanitize_metadata_text(chunk, max_sentences=1).strip()
        if not sentence:
            continue
        if sentence[-1] not in ".!?":
            sentence += "."
        sentences.append(sentence)
    return sentences


def _dedupe_preserve_order(values: List[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for value in values:
        normalized = _normalize_line_breaks(value).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(_normalize_line_breaks(value))
    return ordered


def _choice_from_pool(options: tuple[str, ...], seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


def _normalize_hashtag_token(token: str) -> str:
    cleaned = re.sub(r"[^A-Za-zÀ-ÿ0-9]+", "", str(token or ""))
    return cleaned[:24]


def _build_hashtags(topic_title: str, post_type: str) -> List[str]:
    tags: List[str] = []
    for token in _meaningful_title_tokens(topic_title)[:3]:
        normalized = _normalize_hashtag_token(token.title())
        if normalized:
            tags.append(f"#{normalized}")
    defaults = _FALLBACK_DEFAULT_HASHTAGS.get(str(post_type or "").strip().lower(), _FALLBACK_DEFAULT_HASHTAGS["default"])
    for tag in defaults:
        if tag not in tags:
            tags.append(tag)
    return tags[:4]


def _compress_sentence(text: str, *, max_chars: int, min_chars: int = 0) -> str:
    prepared = re.sub(r"\([^)]*\)", " ", str(text or ""))
    prepared = re.sub(r"\([^)]*$", " ", prepared)
    prepared = prepared.replace("z.B.", "zum Beispiel").replace("u.a.", "unter anderem")
    sentence = sanitize_metadata_text(prepared, max_sentences=1)
    if not sentence:
        return ""
    if len(sentence) <= max_chars:
        if len(sentence) < min_chars:
            sentence = f"{sentence.rstrip('.!?')} und bleibt so im Alltag greifbar."
        return sanitize_metadata_text(sentence, max_sentences=2)

    words = sentence.rstrip(".!?").split()
    trimmed: List[str] = []
    for word in words:
        candidate = " ".join(trimmed + [word]).strip()
        if len(candidate) + 1 > max_chars:
            break
        trimmed.append(word)
    result = " ".join(trimmed).strip()
    if not result:
        result = sentence[: max(0, max_chars - 1)].rsplit(" ", 1)[0].strip()
    while re.search(r"\b(?:oder|und|sowie|ist|es|mit|bei|vom|von|für|fuer|zum|zur|der|die|das)\s*$", result, flags=re.IGNORECASE):
        parts = result.rsplit(" ", 1)
        if len(parts) != 2:
            break
        result = parts[0].strip()
    result = result.rstrip(",;:") + "."
    if len(result) < min_chars:
        result = f"{result.rstrip('.!?')} und bleibt im Alltag wichtig."
    return sanitize_metadata_text(result, max_sentences=2)


def _ensure_minimum_sentence(text: str, minimum_chars: int) -> str:
    sentence = sanitize_metadata_text(text, max_sentences=2)
    if not sentence:
        sentence = "So bleibt Unterstützung im Alltag verlässlich und greifbar."
    if len(sentence) >= minimum_chars:
        return sentence
    expanded = f"{sentence.rstrip('.!?')} So bleibt Unterstützung im Alltag verlässlich und greifbar."
    return sanitize_metadata_text(expanded, max_sentences=2)


def _material_sentences(*texts: Any) -> List[str]:
    sentences: List[str] = []
    for text in texts:
        if isinstance(text, (list, tuple)):
            for item in text:
                sentences.extend(_material_sentences(item))
            continue
        cleaned = sanitize_metadata_text(text, max_sentences=6)
        if not cleaned:
            continue
        sentences.extend(_split_sentences(cleaned))
    return _dedupe_preserve_order(sentences)


def _build_caption_material(
    *,
    topic_title: str,
    post_type: str,
    script: str,
    context: str,
    fallback_facts: Optional[List[str]],
) -> Dict[str, Any]:
    normalized_post_type = str(post_type or "").strip().lower()
    fact_sentences = _material_sentences(sanitize_fact_fragments(list(fallback_facts or [])))
    context_sentences = _material_sentences(context)
    raw_sentences = _dedupe_preserve_order(fact_sentences + context_sentences)
    sentences = [
        sentence
        for sentence in raw_sentences
        if _script_overlap_ratio(script, sentence) <= 0.55 and not _caption_looks_like_title(topic_title, sentence)
    ]
    generic_opener = _choice_from_pool(
        _FALLBACK_OPENERS.get(normalized_post_type, _FALLBACK_OPENERS["default"]),
        f"{topic_title}|{post_type}|opener",
    )
    generic_support = _choice_from_pool(
        _FALLBACK_SUPPORT_SENTENCES.get(normalized_post_type, _FALLBACK_SUPPORT_SENTENCES["default"]),
        f"{topic_title}|{post_type}|support",
    )
    fallback_bullets = list(
        _FALLBACK_BULLETS.get(normalized_post_type, _FALLBACK_BULLETS["default"])
    )
    return {
        "topic_title": topic_title,
        "post_type": normalized_post_type,
        "hashtags": _build_hashtags(topic_title, normalized_post_type),
        "generic_opener": generic_opener,
        "generic_support": generic_support,
        "sentences": sentences,
        "fallback_bullets": fallback_bullets,
    }


def _pick_fact_sentence(material: Dict[str, Any], index: int, *, max_chars: int, min_chars: int = 0) -> str:
    sentences = list(material.get("sentences") or [])
    if index < len(sentences):
        source = sentences[index]
        compressed = _compress_sentence(source, max_chars=max_chars, min_chars=min_chars)
        full = _compress_sentence(source, max_chars=1000, min_chars=min_chars)
        if full and compressed and len(full) > len(compressed) + 20:
            fallback = material["fallback_bullets"][index % len(material["fallback_bullets"])]
            return _compress_sentence(fallback, max_chars=max_chars, min_chars=min_chars)
        return compressed
    fallback = material["fallback_bullets"][index % len(material["fallback_bullets"])]
    return _compress_sentence(fallback, max_chars=max_chars, min_chars=min_chars)


def _build_short_paragraph_variant(material: Dict[str, Any]) -> str:
    hashtags = " ".join(material["hashtags"][:3])
    opener = material["generic_opener"]
    detail = _pick_fact_sentence(material, 0, max_chars=115)
    follow_up = _pick_fact_sentence(material, 1, max_chars=85)
    body = f"{opener} {detail}"
    if len(f"{body} {hashtags}") < 150:
        body = f"{body} {follow_up}"
    result = f"{body} {hashtags}".strip()
    if len(result) > 260:
        result = f"{opener} {_compress_sentence(detail, max_chars=90)} {hashtags}".strip()
    if len(result) < 140:
        result = f"{body} {material['generic_support']} {hashtags}".strip()
    return _normalize_line_breaks(result)


def _build_medium_bullets_variant(material: Dict[str, Any]) -> str:
    hashtags = " ".join(material["hashtags"][:3])
    hook = _compress_sentence(material["generic_opener"], max_chars=95)
    support = _compress_sentence(material["generic_support"], max_chars=120)
    bullets = [
        _ensure_minimum_sentence(_pick_fact_sentence(material, 0, max_chars=100, min_chars=40), 40),
        _ensure_minimum_sentence(_pick_fact_sentence(material, 1, max_chars=100, min_chars=40), 40),
        _ensure_minimum_sentence(_pick_fact_sentence(material, 2, max_chars=100, min_chars=40), 40),
    ]
    body = "\n\n".join(
        [
            f"{hook} {support}".strip(),
            "\n".join(f"• {bullet}" for bullet in bullets),
            hashtags,
        ]
    ).strip()
    if len(body) > 420:
        body = "\n\n".join(
            [
                hook,
                "\n".join(f"• {bullet}" for bullet in bullets[:2]),
                hashtags,
            ]
        ).strip()
    if len(body) < 220:
        extra_bullet = _ensure_minimum_sentence(_pick_fact_sentence(material, 3, max_chars=110, min_chars=40), 40)
        body = "\n\n".join(
            [
                f"{hook} {support}".strip(),
                "\n".join(f"• {bullet}" for bullet in bullets[:2] + [extra_bullet]),
                hashtags,
            ]
        ).strip()
    return _normalize_line_breaks(body)


def _build_long_structured_variant(material: Dict[str, Any]) -> str:
    hashtags = " ".join(material["hashtags"][:4])
    paragraph_one = f"{material['generic_opener']} {_pick_fact_sentence(material, 0, max_chars=120)}".strip()
    paragraph_two = f"{material['generic_support']} {_pick_fact_sentence(material, 1, max_chars=120)}".strip()
    numbered = [
        _ensure_minimum_sentence(_pick_fact_sentence(material, 0, max_chars=105, min_chars=45), 45),
        _ensure_minimum_sentence(_pick_fact_sentence(material, 2, max_chars=105, min_chars=45), 45),
        _ensure_minimum_sentence(_pick_fact_sentence(material, 3, max_chars=105, min_chars=45), 45),
    ]
    if len(material.get("sentences") or []) >= 4:
        numbered.append(_ensure_minimum_sentence(_pick_fact_sentence(material, 4, max_chars=105, min_chars=45), 45))
    body = "\n\n".join(
        [
            paragraph_one,
            paragraph_two,
            "\n".join(f"{index + 1}. {line}" for index, line in enumerate(numbered[:4])),
            hashtags,
        ]
    ).strip()
    if len(body) > 700:
        body = "\n\n".join(
            [
                paragraph_one,
                paragraph_two,
                "\n".join(f"{index + 1}. {line}" for index, line in enumerate(numbered[:3])),
                hashtags,
            ]
        ).strip()
    if len(body) < 350:
        bonus = _ensure_minimum_sentence(_pick_fact_sentence(material, 5, max_chars=115, min_chars=45), 45)
        body = "\n\n".join(
            [
                paragraph_one,
                f"{paragraph_two} {bonus}".strip(),
                "\n".join(f"{index + 1}. {line}" for index, line in enumerate(numbered[:3])),
                hashtags,
            ]
        ).strip()
    return _normalize_line_breaks(body)


def _repair_caption_bundle(
    *,
    bundle: Dict[str, Any],
    topic_title: str,
    post_type: str,
    script: str,
    context: str,
    fallback_facts: Optional[List[str]],
) -> Dict[str, Any]:
    material = _build_caption_material(
        topic_title=topic_title,
        post_type=post_type,
        script=script,
        context=context,
        fallback_facts=fallback_facts,
    )
    raw_by_key = {str(item.get("key") or ""): str(item.get("body") or "").strip() for item in list(bundle.get("variants") or [])}
    repaired: List[Dict[str, str]] = []
    for key in FAMILY_ORDER:
        raw_body = raw_by_key.get(key, "")
        if raw_body:
            try:
                validate_caption_variant(key, raw_body, script)
                repaired.append({"key": key, "body": _normalize_line_breaks(raw_body)})
                continue
            except ValidationError:
                pass
        if key == "short_paragraph":
            rebuilt = _build_short_paragraph_variant(material)
        elif key == "medium_bullets":
            rebuilt = _build_medium_bullets_variant(material)
        else:
            rebuilt = _build_long_structured_variant(material)
        validate_caption_variant(key, rebuilt, script)
        repaired.append({"key": key, "body": rebuilt})
    return {
        "variants": repaired,
        "selected_key": str(bundle.get("selected_key") or "").strip(),
        "selected_body": str(bundle.get("selected_body") or "").strip(),
        "selection_reason": "local_repair",
    }


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
    if len(hashtags) > 6:
        raise ValidationError(message="Caption hashtag count invalid", details={"key": key, "hashtags": hashtags})
    if _count_emojis(normalized) > 1:
        raise ValidationError(message="Caption emoji count invalid", details={"key": key, "emoji_count": _count_emojis(normalized)})
    if _looks_mixed_language(normalized):
        raise ValidationError(message="Caption appears mixed-language", details={"key": key})
    if _script_overlap_ratio(script, normalized) > 0.72:
        raise ValidationError(message="Caption repeats script too closely", details={"key": key})

    if key == "short_paragraph":
        if bullets or numbered:
            raise ValidationError(message="short_paragraph cannot contain bullets or numbering", details={"key": key})
    elif key == "medium_bullets":
        if not bullets and not numbered:
            raise ValidationError(message="medium_bullets must contain bullets or numbered items", details={"key": key})
    elif key == "long_structured":
        if len(paragraphs) < 2:
            raise ValidationError(message="long_structured must contain at least two paragraphs", details={"key": key})

    return {
        "key": key,
        "body": normalized,
        "char_count": char_count,
        "paragraph_count": len(paragraphs),
        "hashtags": hashtags,
    }



def validate_caption_bundle(bundle: Dict[str, Any], script: str, post_type: str = "") -> Dict[str, Any]:
    variants = list(bundle.get("variants") or [])
    by_key = {str(item.get("key") or ""): item for item in variants}
    required_pool = set(_caption_variant_pool(post_type)) if post_type else set(FAMILY_ORDER)
    available_required = required_pool & set(by_key.keys())
    if not available_required:
        raise ValidationError(
            message=f"No usable caption variants in LLM response (need one of: {', '.join(sorted(required_pool))})",
            details={"required": sorted(required_pool), "available_keys": list(by_key.keys())},
        )
    validated = []
    for key in FAMILY_ORDER:
        body = str((by_key.get(key) or {}).get("body") or "").strip()
        if not body:
            continue
        validated.append(validate_caption_variant(key, body, script))
    if not validated:
        raise ValidationError(message="Caption bundle has no valid variants", details={"keys": list(by_key.keys())})
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



def _select_best_variant(
    variants: List[Dict[str, Any]], preferred_key: str, topic_title: str,
) -> tuple:
    by_key = {v["key"]: v["body"] for v in variants}
    if preferred_key in by_key and not _caption_looks_like_title(topic_title, by_key[preferred_key]):
        return preferred_key, by_key[preferred_key]
    for key in FAMILY_ORDER:
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
    context: str,
    llm_factory: Optional[Callable] = None,
    canonical_topic: Optional[str] = None,
    fallback_facts: Optional[List[str]] = None,
    allow_repair: bool = False,
) -> Dict[str, Any]:
    canonical_topic = str(canonical_topic or topic_title or "").strip()
    selected_key = select_caption_variant_key(topic_title=canonical_topic, post_type=post_type, script=script)
    llm_factory = llm_factory or get_llm_client
    llm = llm_factory()
    prompt = _build_caption_prompt(topic_title=canonical_topic, post_type=post_type, script=script, context=context)
    last_error: Optional[ValidationError] = None
    for attempt in range(3):
        try:
            raw_text = llm.generate_gemini_text(
                prompt=prompt,
                system_prompt=(
                    "Du bist ein Social-Media-Texter fuer barrierefreie Inhalte. "
                    "Antworte ausschliesslich im Markerformat mit [short_paragraph], [medium_bullets] und [long_structured]. "
                    "Keine Erklaerungen, kein JSON, kein Markdown."
                ),
                max_tokens=2500,
                temperature=0.8,
            )
            parsed = _parse_text_variants(raw_text)
            if allow_repair:
                repaired = _repair_caption_bundle(
                    bundle=parsed,
                    topic_title=canonical_topic,
                    post_type=post_type,
                    script=script,
                    context=context,
                    fallback_facts=fallback_facts,
                )
                bundle = validate_caption_bundle(repaired, script, post_type=post_type)
            else:
                bundle = validate_caption_bundle(parsed, script, post_type=post_type)
            preferred_pool = _caption_variant_pool(post_type)
            if not bundle["selected_key"] or bundle["selected_key"] not in preferred_pool:
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
        except ValidationError as exc:
            last_error = exc
            logger.warning(
                "caption_generation_retry",
                topic_title=canonical_topic[:60],
                attempt=attempt + 1,
                error=exc.message,
                details=str(exc.details)[:200],
            )
            prompt = f"{prompt}\n\nFEEDBACK: {exc.message}. Details: {json.dumps(exc.details, ensure_ascii=False)[:800]}"
    raise ValidationError(
        message="Caption generation failed after 3 attempts",
        details={"topic_title": canonical_topic, "last_error": last_error.message if last_error else None},
    )



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
    script = str(payload.get("dialog_script") or payload.get("script") or script_fallback or "").strip()
    if not script:
        return payload
    strict_seed = payload.get("strict_seed") or {}
    fallback_facts = sanitize_fact_fragments(list(strict_seed.get("facts") or []))
    context_candidates = [
        context,
        payload.get("source_summary"),
        payload.get("description"),
        payload.get("research_caption"),
        strict_seed.get("source_context"),
    ]
    derived_context_parts = _dedupe_preserve_order(
        [sanitize_metadata_text(value, max_sentences=3) for value in context_candidates if sanitize_metadata_text(value, max_sentences=3)]
        + fallback_facts[:4]
    )
    derived_context = " ".join(derived_context_parts).strip()
    if not derived_context:
        derived_context = sanitize_metadata_text(script, max_sentences=3)
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
        fallback_facts=fallback_facts,
        allow_repair=True,
    )
    payload["canonical_topic"] = canonical_topic
    payload["caption_bundle"] = bundle
    payload["description"] = bundle["selected_body"]
    payload["caption"] = bundle["selected_body"]
    return payload
