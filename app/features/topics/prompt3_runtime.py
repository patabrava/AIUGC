"""
Product Prompt 3 runtime.
"""

from __future__ import annotations

import math
import re
import time
from typing import Callable, Dict, List, Optional

from app.adapters.llm_client import get_llm_client
from app.core.errors import ThirdPartyError, ValidationError
from app.core.logging import get_logger
from app.core.video_profiles import get_duration_profile, script_word_count
from app.features.topics.content_utils import strip_cta_from_script
from app.features.topics.product_knowledge import get_product_knowledge_base, plan_product_mix
from app.features.topics.prompts import build_prompt3
from app.features.topics.response_parsers import parse_prompt3_response
from app.features.topics.topic_validation import (
    count_spoken_sentences,
    estimate_script_duration_seconds,
    get_prompt3_word_bounds,
    get_prompt3_sentence_bounds,
    sanitize_spoken_fragment,
    trim_spoken_script_to_word_bounds,
    validate_german_only_text,
)


_INACTIVE_PRODUCT_MARKERS = ("LL12", "Konstanz")
_BRAND_MARKERS = ("LIPPE Lift", "Lippe Lift", "Lipperlift", "lippelift.de")
_SALES_COPY_MARKERS = (
    "frag nach",
    "frag jetzt",
    "lass dir zeigen",
    "jetzt mehr erfahren",
    "mehr erfahren",
    "buche",
    "kauf",
    "kaufe",
    "melde dich",
)
_RETRYABLE_PROVIDER_STATUS_CODES = {429, 500, 503}
logger = get_logger(__name__)


def _normalize(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _matches_entry(product_name: str, aliases: List[str], candidate_name: str) -> bool:
    normalized = _normalize(candidate_name)
    if normalized == _normalize(product_name):
        return True
    return normalized in {_normalize(alias) for alias in aliases if alias}


def _user_facing_product_markers(product_name: str, aliases: List[str]) -> List[str]:
    markers: List[str] = []
    for value in [product_name, *aliases, *_BRAND_MARKERS]:
        normalized = " ".join(str(value or "").strip().split())
        if normalized and _normalize(normalized) not in {_normalize(marker) for marker in markers}:
            markers.append(normalized)
        for token in re.findall(r"\b[A-ZÄÖÜ]{1,6}\d{1,4}[A-Z0-9]*\b", str(value or "")):
            if _normalize(token) not in {_normalize(marker) for marker in markers}:
                markers.append(token)
    return markers


def _find_user_facing_product_marker(*, angle: str, script: str, cta: str, product_name: str, aliases: List[str]) -> str:
    haystack = _normalize(" ".join([angle, script, cta]))
    for marker in _user_facing_product_markers(product_name, aliases):
        if _normalize(marker) in haystack:
            return marker
    return ""


def _find_sales_copy_marker(*, angle: str, script: str, cta: str) -> str:
    haystack = _normalize(" ".join([angle, script, cta]))
    for marker in _SALES_COPY_MARKERS:
        normalized_marker = _normalize(marker)
        if re.search(rf"(?<!\w){re.escape(normalized_marker)}(?!\w)", haystack):
            return marker
    return ""


_USER_FACING_TOPIC_FIELDS = ("title", "angle", "script", "rotation", "cta")


def _strip_user_facing_product_markers(text: str, *, product_name: str, aliases: List[str]) -> str:
    """Remove every product, model, marketing and brand name from user-facing text."""
    cleaned = str(text or "")
    if not cleaned.strip():
        return ""
    # Remove longest markers first so multi-word names go before their sub-tokens.
    for marker in sorted(_user_facing_product_markers(product_name, aliases), key=len, reverse=True):
        marker = marker.strip()
        if not marker:
            continue
        cleaned = re.sub(rf"(?<!\w){re.escape(marker)}(?!\w)", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" :-,–—;").strip()


def _enforce_product_name_privacy(
    topic: Dict[str, object], *, product_name: str, aliases: List[str]
) -> Dict[str, object]:
    """Guarantee that no user-facing field ever exposes a product name, whatever the source path."""
    for field in _USER_FACING_TOPIC_FIELDS:
        value = topic.get(field)
        if isinstance(value, str) and value:
            topic[field] = _strip_user_facing_product_markers(
                value, product_name=product_name, aliases=aliases
            )
    if not str(topic.get("angle") or "").strip():
        topic["angle"] = "verlässliche Lösung für zuhause"
    if not str(topic.get("title") or "").strip():
        topic["title"] = "Verlässliche Lösung für zuhause"
    return topic


def _is_retryable_provider_error(exc: ThirdPartyError) -> bool:
    details = exc.details if isinstance(exc.details, dict) else {}
    status_code = details.get("status_code")
    try:
        return int(status_code) in _RETRYABLE_PROVIDER_STATUS_CODES
    except (TypeError, ValueError):
        return False


def _build_product_fallback_script(entry, *, target_length_tier: int) -> str:
    fact = sanitize_spoken_fragment((entry.facts or [entry.summary])[0], ensure_terminal=False).rstrip(".!?")
    if not fact:
        fact = "die Lösung deinen Alltag zuhause besser planbar macht"
    if target_length_tier <= 8:
        script = f"Ein Plattformlift hilft dir zuhause, weil {fact} und dein Alltag dadurch ruhiger planbar bleibt."
    elif target_length_tier <= 16:
        script = (
            f"Ein Plattformlift hilft dir zuhause. {fact} bleibt der zentrale Vorteil. "
            "So wird deine Treppe ruhiger, verlässlicher und ohne unnötigen Umbau besser planbar."
        )
    else:
        script = (
            f"Ein Plattformlift hilft dir zuhause. {fact} bleibt der zentrale Vorteil. "
            "Du siehst früh, welche Variante zu deiner Treppe passt, welche Bedienung sich richtig anfühlt "
            "und welche Details du vor dem Einbau klären solltest. "
            "Das gibt dir mehr Sicherheit auf Wegen, die jeden Tag zählen. "
            "Die Planung bleibt klar, verlässlich und alltagstauglich. So wird dein Zuhause ohne unnötigen Umbau besser nutzbar."
        )
    min_words, max_words = get_prompt3_word_bounds(target_length_tier)
    cleaned = sanitize_spoken_fragment(script, ensure_terminal=True)
    if script_word_count(cleaned) > max_words:
        cleaned = trim_spoken_script_to_word_bounds(cleaned, min_words=min_words, max_words=max_words)
    while script_word_count(cleaned) < min_words:
        cleaned = sanitize_spoken_fragment(
            f"{cleaned.rstrip('.!?')} und bleibt dadurch im Alltag besser nutzbar.",
            ensure_terminal=True,
        )
        if script_word_count(cleaned) > max_words:
            cleaned = trim_spoken_script_to_word_bounds(cleaned, min_words=min_words, max_words=max_words)
            break
    return cleaned


def _build_product_fallback_topic(entry, *, target_length_tier: int, reason: str) -> Dict[str, object]:
    script = _build_product_fallback_script(entry, target_length_tier=target_length_tier)
    cta = "So bleibt die Entscheidung näher an deinem echten Alltag."
    validate_german_only_text(script, field_name="script", context="prompt3_fallback")
    validate_german_only_text(cta, field_name="cta", context="prompt3_fallback")
    rotation = strip_cta_from_script(script, cta) or script
    return _enforce_product_name_privacy(
        {
            "title": "Verlässliche Lösung für zuhause",
            "rotation": rotation,
            "cta": cta,
            "spoken_duration": max(1, int(estimate_script_duration_seconds(script) or math.ceil(len(script.split()) / 2.6))),
            "script": script,
            "framework": "PAL",
            "product_name": entry.product_name,
            "angle": "verlässliche Lösung für zuhause",
            "facts": list(entry.facts[:5]),
            "source_summary": entry.summary,
            "support_facts": entry.support_facts,
            "generation_mode": "synthetic_fallback",
            "fallback_reason": reason,
        },
        product_name=entry.product_name,
        aliases=entry.aliases,
    )


def generate_product_topics(
    *,
    count: int = 1,
    seed: Optional[int] = None,
    target_length_tier: Optional[int] = None,
    llm_factory: Callable = get_llm_client,
) -> List[Dict[str, object]]:
    profile = get_duration_profile(target_length_tier or 8)
    entries = get_product_knowledge_base()
    if not entries:
        raise ValidationError(
            message="No active product knowledge available",
            details={"target_length_tier": profile.target_length_tier},
        )

    planned_entries = plan_product_mix(entries, count=count, seed=seed)
    llm = llm_factory()
    results: List[Dict[str, object]] = []

    for entry in planned_entries:
        prompt = build_prompt3(product=entry, profile=profile)
        last_error = ""
        for attempt in range(3):
            try:
                response_text = llm.generate_gemini_text(
                    prompt=prompt,
                    system_prompt=None,
                    max_tokens=1200,
                    thinking_budget=0,
                )
            except (ThirdPartyError, ValidationError) as exc:
                last_error = getattr(exc, "message", str(exc))
                if isinstance(exc, ThirdPartyError) and _is_retryable_provider_error(exc) and attempt < 2:
                    time.sleep(min(2 * (attempt + 1), 6))
                continue
            min_words, max_words = get_prompt3_word_bounds(profile.target_length_tier)
            try:
                candidate = parse_prompt3_response(
                    response_text,
                    fallback_product_name=entry.product_name,
                    fallback_facts=entry.facts,
                )
            except ValidationError as exc:
                last_error = exc.message
                prompt = (
                    f"{prompt}\n\nRÜCKMELDUNG: Der letzte Entwurf war noch nicht klar genug. "
                    "Nutze eine Zeile pro Feld: Produkt, Winkel, Sprechtext, Schlusssatz, Fakten. "
                    "Keine Einleitung, kein Fließtext und keine Anglizismen."
                )
                continue
            if not _matches_entry(entry.product_name, entry.aliases, candidate.product_name):
                last_error = f"Falsches Produkt genannt: {candidate.product_name}"
                prompt = f"{prompt}\n\nFEEDBACK: {last_error}. Nenne nur {entry.product_name}."
                continue
            leaked_marker = _find_user_facing_product_marker(
                angle=candidate.angle,
                script=candidate.script,
                cta=candidate.cta,
                product_name=entry.product_name,
                aliases=entry.aliases,
            )
            if leaked_marker:
                last_error = f"Produktname in Nutzertext genannt: {leaked_marker}"
                prompt = (
                    f"{prompt}\n\nFEEDBACK: {last_error}. Das Feld Produkt bleibt intern, aber nenne "
                    "Produktnamen, Modellnamen, Quellnamen oder die Marke nicht in Winkel, Sprechtext oder "
                    "Schlusssatz. Schreibe über die Erfahrung mit dem Plattformlift, zuverlässige "
                    "Nutzung, deutsche Fertigung und Alltagstauglichkeit."
                )
                continue
            sales_marker = _find_sales_copy_marker(angle=candidate.angle, script=candidate.script, cta=candidate.cta)
            if sales_marker:
                last_error = f"Verkaufsaufforderung im Nutzertext: {sales_marker}"
                prompt = (
                    f"{prompt}\n\nFEEDBACK: {last_error}. Schreibe keine Verkaufsaufforderung. "
                    "Der Schlusssatz muss wie ein ruhiger Alltagsgedanke klingen, nicht wie ein Aufruf zur Anfrage, "
                    "Beratung, Buchung oder zum Kauf."
                )
                continue
            if any(marker.lower() in candidate.script.lower() for marker in _INACTIVE_PRODUCT_MARKERS):
                last_error = "Inactive product marker leaked into script"
                prompt = f"{prompt}\n\nFEEDBACK: Verwende keine ausgeschlossenen Produkte. Nenne nur {entry.product_name}."
                continue

            normalized_script = sanitize_spoken_fragment(candidate.script, ensure_terminal=True)
            try:
                validate_german_only_text(normalized_script, field_name="script", context="prompt3")
                validate_german_only_text(candidate.cta, field_name="cta", context="prompt3")
            except ValidationError as exc:
                last_error = exc.message
                prompt = (
                    f"{prompt}\n\nRÜCKMELDUNG: Der Sprechtext oder der Schlusssatz enthält Anglizismen. "
                    "Schreibe beides rein deutsch. Ersetze englische Lehnwörter durch natürliche deutsche Wörter. "
                    f"Details: {exc.details}"
                )
                continue
            normalized_word_count = script_word_count(normalized_script)
            min_sentences, max_sentences = get_prompt3_sentence_bounds(profile.target_length_tier)
            sentence_count = count_spoken_sentences(normalized_script)
            if normalized_word_count < min_words:
                last_error = f"PROMPT_3 script too short: {normalized_word_count} words"
                prompt = (
                    f"{prompt}\n\nFEEDBACK: Der Scripttext ist noch zu kurz. "
                    f"Halte dich für {entry.product_name} an etwa {min_words}-{max_words} Wörter "
                    "und gib dem Produkt mehr Substanz."
                )
                continue
            if sentence_count < min_sentences or sentence_count > max_sentences:
                last_error = f"PROMPT_3 sentence count out of range: {sentence_count}"
                prompt = (
                    f"{prompt}\n\nFEEDBACK: Der Scripttext braucht für {entry.product_name} "
                    f"etwa {min_sentences}-{max_sentences} Sätze. "
                    "Schreibe jeden Satz klar und vollständig, ohne die Antwort abzukürzen."
                )
                continue
            if normalized_word_count > max_words:
                normalized_script = trim_spoken_script_to_word_bounds(
                    normalized_script,
                    min_words=min_words,
                    max_words=max_words,
                )
                normalized_word_count = script_word_count(normalized_script)
                if normalized_word_count < min_words:
                    last_error = f"PROMPT_3 trim fell below minimum length: {normalized_word_count} words"
                    prompt = (
                        f"{prompt}\n\nFEEDBACK: Der Scripttext ist noch zu lang oder zu kurz. "
                        f"Halte dich für {entry.product_name} an etwa {min_words}-{max_words} Wörter."
                    )
                    continue
                candidate.estimated_duration_s = estimate_script_duration_seconds(normalized_script)
            candidate.script = normalized_script

            rotation = strip_cta_from_script(candidate.script, candidate.cta) or candidate.script.strip()
            results.append(
                _enforce_product_name_privacy(
                    {
                        "title": candidate.angle,
                        "rotation": rotation,
                        "cta": candidate.cta,
                        "spoken_duration": max(
                            1,
                            int(candidate.estimated_duration_s or math.ceil(len(candidate.script.split()) / 2.6)),
                        ),
                        "script": candidate.script,
                        "framework": candidate.framework,
                        "product_name": entry.product_name,
                        "angle": candidate.angle,
                        "facts": candidate.facts,
                        "source_summary": entry.summary,
                        "support_facts": entry.support_facts,
                    },
                    product_name=entry.product_name,
                    aliases=entry.aliases,
                )
            )
            break
        else:
            logger.warning(
                "product_topic_fallback_synthesized",
                product_name=entry.product_name,
                target_length_tier=profile.target_length_tier,
                reason=last_error or "provider_retry_exhausted",
            )
            results.append(
                _build_product_fallback_topic(
                    entry,
                    target_length_tier=profile.target_length_tier,
                    reason=last_error or "provider_retry_exhausted",
                )
            )

    return results
