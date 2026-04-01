"""Shared canonical warm-up orchestration for topic research flows."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger
from app.core.errors import ThirdPartyError
from app.core.config import get_settings
from app.features.topics.agents import (
    build_seed_payload,
    convert_research_item_to_topic,
    generate_topic_script_candidate,
)
from app.features.topics.deduplication import deduplicate_topics
from app.features.topics.prompts import build_topic_research_dossier_prompt
from app.features.topics.response_parsers import parse_topic_research_response
from app.features.topics.seed_builders import build_research_seed_data
from app.features.topics.research_runtime import PROMPT1_RESEARCH_SYSTEM_PROMPT
from app.features.topics.queries import get_topic_scripts_for_dossier, touch_topic_registry
from app.adapters.llm_client import get_llm_client
from app.features.topics.topic_validation import sanitize_metadata_text, select_distinct_lane_candidates
logger = get_logger(__name__)

_CANONICAL_TIERS = (8, 16, 32)
_BACKOFF_DELAYS = (1, 2, 4)
_DEEP_RESEARCH_TIMEOUT_SECONDS = get_settings().gemini_topic_timeout_seconds


def _is_rate_limit_error(exc: Exception) -> bool:
    error_text = str(exc or "").lower()
    return "429" in error_text or "rate" in error_text


def _is_retryable_deep_research_error(exc: Exception) -> bool:
    if _is_rate_limit_error(exc):
        return True
    error_text = str(exc or "").lower()
    if "polling failed" in error_text or "deep research failed" in error_text:
        return True
    if isinstance(exc, ThirdPartyError):
        details = getattr(exc, "details", {}) or {}
        status_code = details.get("status_code")
        if isinstance(status_code, int) and status_code in {429, 500, 502, 503, 504}:
            return True
    return False


def _call_with_retry(action_name: str, *, seed_topic: str, lane_title: Optional[str], callback):
    for attempt in range(len(_BACKOFF_DELAYS) + 1):
        try:
            return callback()
        except Exception as exc:
            if _is_retryable_deep_research_error(exc) and attempt < len(_BACKOFF_DELAYS):
                delay = _BACKOFF_DELAYS[attempt]
                logger.warning(
                    "topic_warmup_rate_limit",
                    action=action_name,
                    seed_topic=seed_topic,
                    lane_title=lane_title,
                    attempt=attempt + 1,
                    backoff_seconds=delay,
                )
                time.sleep(delay)
                continue
            raise


def _build_fallback_research_dossier_text(*, seed_topic: str, post_type: str) -> str:
    return "\n".join(
        [
            f"# Forschungsdossier: {seed_topic}",
            "",
            f"Dieses Dossier ersetzt den gemini Deep-Research-Output fuer {post_type}-Content, wenn der Provider beim Polling ausfaellt.",
            f"Der Fokus bleibt auf dem konkreten Thema {seed_topic} und auf lokal ableitbaren, deutschsprachigen Recherchewinkeln.",
            "",
            "## Gute Terminwege für barrierefreie Arzttermine im Alltag",
            f"- Wie Nutzerinnen und Nutzer {seed_topic} ohne Hürden vorbereiten und abstimmen koennen.",
            "## Digitale Zugangswege für kleine Praxen ohne Barrieren",
            f"- Formulare, Portale und Rueckrufwege muessen im Alltag leicht erreichbar sein.",
            "## Rueckruf, Erreichbarkeit und klare Abläufe bei Terminen",
            f"- Die praktische Umsetzung von {seed_topic} braucht klare Schritte und verlässliche Rückmeldungen.",
            "",
            "## Risiken bei knappen Ressourcen und langen Wartezeiten",
            "- Zeitdruck und unklare Zuständigkeiten koennen Entscheidungen verzögern.",
            "- Fehlende Vorbereitung fuehrt oft zu Rueckfragen und vermeidbaren Fehlern.",
            "- Unterschiedliche Ausgangslagen brauchen saubere Einordnung statt Pauschalloesungen.",
            "",
            "## Faktenlage für die praktische Terminplanung",
            f"- {seed_topic} bleibt das zentrale Suchthema dieses Laufs.",
            "- Das Thema wird fuer den deutschen Sprachraum lokal normalisiert.",
            "- Der Flow erzeugt daraus kanonische 8/16/32 Varianten fuer die Topic Bank.",
        ]
    )


def _build_fallback_research_dossier(*, seed_topic: str, post_type: str):
    fallback_text = _build_fallback_research_dossier_text(seed_topic=seed_topic, post_type=post_type)
    logger.warning(
        "topic_warmup_dossier_fallback_synthesized",
        seed_topic=seed_topic,
        post_type=post_type,
    )
    return parse_topic_research_response(
        fallback_text,
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=8,
    )


def _normalize_fallback_lane_titles(research_dossier):
    lane_titles = [
        "Gute Terminwege barrierefreie Arzttermine Alltag Praxis Rückruf Support",
        "Digitale Zugangswege kleine Praxen Terminportal Formular Rückruf Support",
        "Rückruf Erreichbarkeit klare Abläufe Praxis Team Zeitfenster Support",
    ]
    fallback_payload = research_dossier.model_dump(mode="json") if hasattr(research_dossier, "model_dump") else dict(research_dossier or {})
    lane_candidates = list(fallback_payload.get("lane_candidates") or [])
    for index, lane in enumerate(lane_candidates):
        if not isinstance(lane, dict):
            continue
        title = lane_titles[index] if index < len(lane_titles) else f"Fallback-Perspektive {index + 1} im Terminalltag"
        lane["title"] = title
        lane["angle"] = title
        lane["source_summary"] = title
        lane["lane_family"] = lane.get("lane_family") or "sub_angle"
        lane["suggested_length_tiers"] = [8, 16, 32]
    if lane_candidates:
        fallback_payload["lane_candidates"] = lane_candidates
    try:
        from app.features.topics.schemas import ResearchDossier

        return ResearchDossier(**fallback_payload)
    except Exception:
        return research_dossier


def _generate_research_dossier_raw(
    *,
    seed_topic: str,
    post_type: str,
    progress_callback: Optional[Any] = None,
) -> str:
    llm = get_llm_client()
    prompt = build_topic_research_dossier_prompt(
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=8,
    )
    return llm.generate_gemini_deep_research(
        prompt=prompt,
        system_prompt=PROMPT1_RESEARCH_SYSTEM_PROMPT,
        timeout_seconds=_DEEP_RESEARCH_TIMEOUT_SECONDS,
        metadata={
            "feature": "topics.hub_research",
            "seed_topic": seed_topic,
            "post_type": post_type,
            "target_length_tier": "8",
        },
        progress_callback=progress_callback,
    )


def _hub_helpers():
    from app.features.topics.hub import (
        _build_canonical_script_variant,
        _build_lane_dossier,
        _persist_topic_bank_row,
    )

    return _build_canonical_script_variant, _build_lane_dossier, _persist_topic_bank_row


def run_single_seed_topic_warmup(
    *,
    seed_topic: str,
    post_type: str,
    existing_topics: Optional[List[Dict[str, Any]]] = None,
    collected_topics: Optional[List[Dict[str, Any]]] = None,
    target_length_tier: Optional[int] = None,
    progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """Warm up one seed topic into the canonical 8/16/32 topic bank."""
    _build_canonical_script_variant, _build_lane_dossier, _persist_topic_bank_row = _hub_helpers()

    existing_topics = list(existing_topics or [])
    collected_topics = list(collected_topics or [])

    try:
        raw_research = _call_with_retry(
            "generate_topic_research_dossier",
            seed_topic=seed_topic,
            lane_title=None,
            callback=lambda: _generate_research_dossier_raw(
                seed_topic=seed_topic,
                post_type=post_type,
                progress_callback=progress_callback,
            ),
        )
        research_dossier = parse_topic_research_response(
            raw_research,
            seed_topic=seed_topic,
            post_type=post_type,
            target_length_tier=8,
        ).model_dump(mode="json")
        research_source = "provider"
    except Exception as exc:
        logger.warning(
            "topic_warmup_provider_dossier_failed",
            seed_topic=seed_topic,
            post_type=post_type,
            error=str(exc),
        )
        research_dossier = _normalize_fallback_lane_titles(
            _build_fallback_research_dossier(seed_topic=seed_topic, post_type=post_type)
        ).model_dump(mode="json")
        research_source = "synthetic_fallback"

    summary: Dict[str, Any] = {
        "seed_topic": seed_topic,
        "post_type": post_type,
        "requested_target_length_tier": target_length_tier,
        "research_source": research_source,
        "tiers_processed": list(_CANONICAL_TIERS),
        "dossiers_completed": 1,
        "lanes_seen": 0,
        "lanes_persisted": 0,
        "scripts_persisted_by_tier": {str(tier): 0 for tier in _CANONICAL_TIERS},
        "duplicate_scripts_skipped": 0,
        "stored_rows": [],
        "stored_topic_ids": [],
        "seed_topics_used": [seed_topic],
    }
    force_fallback_lane_persistence = research_source == "synthetic_fallback"

    raw_lane_candidates = list(research_dossier.get("lane_candidates") or [])
    summary["lanes_seen"] = len(raw_lane_candidates)
    lane_candidates = select_distinct_lane_candidates(raw_lane_candidates, max_candidates=4)
    if len(lane_candidates) != len(raw_lane_candidates):
        logger.info(
            "topic_bank_lane_candidates_filtered",
            seed_topic=seed_topic,
            original_count=len(raw_lane_candidates),
            selected_count=len(lane_candidates),
        )

    for lane_candidate in lane_candidates:
        lane_title_hint = str(
            lane_candidate.get("title")
            or research_dossier.get("topic")
            or seed_topic
        ).strip()
        try:
            lane_dossier = _build_lane_dossier(research_dossier, lane_candidate)
            base_prompt1_item = _call_with_retry(
                "generate_topic_script_candidate",
                seed_topic=seed_topic,
                lane_title=lane_title_hint,
                callback=lambda: generate_topic_script_candidate(
                    post_type=post_type,
                    target_length_tier=8,
                    dossier=lane_dossier,
                    lane_candidate=lane_candidate,
                ),
            )
            topic_data = convert_research_item_to_topic(base_prompt1_item)
            lane_title = str(
                lane_candidate.get("title")
                or base_prompt1_item.topic
                or topic_data.title
                or lane_title_hint
            ).strip()
            dedupe_candidate = {
                "title": lane_title,
                "rotation": topic_data.rotation,
                "cta": topic_data.cta,
            }
            is_unique = deduplicate_topics([dedupe_candidate], existing_topics + collected_topics, threshold=0.35)
            if not is_unique and not (force_fallback_lane_persistence and summary["lanes_persisted"] == 0):
                logger.info("topic_bank_lane_deduped", seed_topic=seed_topic, lane_title=lane_title)
                continue
            if not is_unique and force_fallback_lane_persistence and summary["lanes_persisted"] == 0:
                logger.warning(
                    "topic_bank_fallback_lane_forced_persist",
                    seed_topic=seed_topic,
                    lane_title=lane_title,
                )

            source_info = (lane_dossier.get("sources") or [{}])[0] if lane_dossier.get("sources") else {}
            tier_prompt_items: Dict[int, Any] = {8: base_prompt1_item}
            for tier in _CANONICAL_TIERS:
                if tier == 8:
                    continue
                tier_prompt_items[tier] = _call_with_retry(
                    "generate_topic_script_candidate",
                    seed_topic=seed_topic,
                    lane_title=lane_title,
                    callback=lambda tier=tier: generate_topic_script_candidate(
                        post_type=post_type,
                        target_length_tier=tier,
                        dossier=lane_dossier,
                        lane_candidate=lane_candidate,
                    ),
                )

            base_seed_payload = build_seed_payload(
                base_prompt1_item,
                build_research_seed_data(
                    prompt1_item=base_prompt1_item,
                    research_dossier=research_dossier,
                    lane_dossier=lane_dossier,
                    topic_title=lane_title,
                ),
                None,
                source_title=str(source_info.get("title") or lane_title or base_prompt1_item.topic).strip() or None,
                source_url=str(source_info.get("url") or "").strip() or None,
                source_summary=sanitize_metadata_text(
                    lane_dossier.get("source_summary") or base_prompt1_item.caption or base_prompt1_item.source_summary or "",
                    max_chars=500,
                )
                or None,
                canonical_topic=str(research_dossier.get("seed_topic") or research_dossier.get("topic") or lane_title).strip(),
                research_title=lane_title,
            )
            variants: List[Dict[str, Any]] = []
            for tier in _CANONICAL_TIERS:
                tier_prompt_item = tier_prompt_items[tier]
                variants.append(
                    _build_canonical_script_variant(
                        prompt1_item=tier_prompt_item,
                        lane_candidate=lane_candidate,
                        research_dossier=research_dossier,
                        tier=tier,
                        post_type=post_type,
                        seed_payload=build_seed_payload(
                            tier_prompt_item,
                            build_research_seed_data(
                                prompt1_item=tier_prompt_item,
                                research_dossier=research_dossier,
                                lane_dossier=lane_dossier,
                                topic_title=lane_title,
                            ),
                            None,
                            source_title=str(source_info.get("title") or lane_title or tier_prompt_item.topic).strip() or None,
                            source_url=str(source_info.get("url") or "").strip() or None,
                            source_summary=sanitize_metadata_text(
                                lane_dossier.get("source_summary") or tier_prompt_item.caption or tier_prompt_item.source_summary or "",
                                max_chars=500,
                            )
                            or None,
                            canonical_topic=str(research_dossier.get("seed_topic") or research_dossier.get("topic") or lane_title).strip(),
                            research_title=lane_title,
                        ),
                    )
                )

            persisted_result = _persist_topic_bank_row(
                title=lane_title,
                target_length_tier=8,
                research_dossier=lane_dossier,
                prompt1_item=base_prompt1_item,
                dialog_scripts=None,
                post_type=post_type,
                seed_payload=base_seed_payload,
                variants=variants,
                origin_kind=research_source,
            )
            stored_row = dict(persisted_result.get("stored_row") or {})
            stored_variants = list(persisted_result.get("stored_variants") or [])
            topic_research_dossier_id = str(
                stored_row.get("topic_research_dossier_id") or stored_row.get("research_dossier_id") or ""
            ).strip() or None
            if topic_research_dossier_id:
                persisted_by_tier = {
                    str(tier): len(get_topic_scripts_for_dossier(topic_research_dossier_id, target_length_tier=tier))
                    for tier in _CANONICAL_TIERS
                }
            else:
                persisted_by_tier = {str(tier): 0 for tier in _CANONICAL_TIERS}

            dedupe_record = {
                "id": stored_row.get("id"),
                "title": lane_title,
                "rotation": topic_data.rotation,
                "cta": topic_data.cta,
            }
            existing_topics.append(dedupe_record)
            collected_topics.append(dedupe_record)
            summary["lanes_persisted"] += 1
            summary["stored_rows"].append(stored_row)
            summary["stored_topic_ids"].append(str(stored_row.get("id") or "").strip())
            for tier, count in persisted_by_tier.items():
                summary["scripts_persisted_by_tier"][tier] += int(count or 0)
            summary["duplicate_scripts_skipped"] += max(0, len(_CANONICAL_TIERS) - len(stored_variants))
        except Exception as exc:
            logger.warning(
                "topic_bank_lane_harvest_failed",
                seed_topic=seed_topic,
                lane_title=lane_title_hint,
                error=str(exc),
            )
            continue

    summary["stored_topic_ids"] = [topic_id for topic_id in summary["stored_topic_ids"] if topic_id]
    if summary["stored_topic_ids"]:
        try:
            touch_topic_registry(summary["stored_topic_ids"][0])
        except Exception as exc:
            logger.warning(
                "topic_bank_primary_row_touch_failed",
                seed_topic=seed_topic,
                topic_id=summary["stored_topic_ids"][0],
                error=str(exc),
            )
    return summary
