"""Guarded SEO opportunity catalog for topics, research dossiers, and blogs."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import get_settings

_DATA_DIR = Path(__file__).resolve().parent / "prompt_data"
_KEYWORD_CATALOG_PATH = _DATA_DIR / "seo_keyword_catalog.json"
_INTERNAL_LINK_CATALOG_PATH = _DATA_DIR / "internal_link_catalog.json"
_TOKEN_RE = re.compile(r"[\wäöüß]+", re.IGNORECASE)
_DEFAULT_AUDIENCE = "Menschen mit Mobilitätseinschränkungen, Angehörige und planende Personen"
_DEFAULT_AVOID_TERMS = [
    "garantiert",
    "heilbar",
    "risikofrei",
    "immer die beste Lösung",
    "100 % Zuschuss",
]


def seo_catalog_enabled() -> bool:
    return bool(get_settings().seo_topic_catalog_enabled)


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _tokens(value: Any) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(str(value or "")) if len(token) > 1}


def derive_primary_keyword(topic: str) -> str:
    """Derive a compact keyphrase from an editorial topic title.

    Generated topic titles often append an angle after a colon or spaced dash.
    That complete title cannot also satisfy the shorter slug and meta-title
    contracts, so the leading subject clause is the safe derived keyphrase.
    """
    normalized = _normalize(topic)
    leading_clause = re.split(r"\s+[-–—]\s+|[:?!]", normalized, maxsplit=1)[0].strip(" ,.;-")
    words = leading_clause.split()
    if not words:
        return normalized

    selected: List[str] = []
    for word in words:
        candidate = " ".join([*selected, word])
        if selected and (len(selected) >= 5 or len(candidate) > 48):
            break
        selected.append(word)
    return " ".join(selected) or normalized


def _has_natural_token(topic_tokens: set[str], keyword_token: str) -> bool:
    grammatical_suffixes = ("e", "en", "er", "es", "em", "heit", "keit")
    return any(
        topic_token == keyword_token
        or (len(keyword_token) >= 5 and topic_token.startswith(keyword_token))
        or (
            len(topic_token) >= 5
            and keyword_token.startswith(topic_token)
            and keyword_token[len(topic_token) :] in grammatical_suffixes
        )
        for topic_token in topic_tokens
    )


@lru_cache(maxsize=1)
def load_keyword_catalog() -> Dict[str, Any]:
    with _KEYWORD_CATALOG_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    entries = list(payload.get("entries") or [])
    if len(entries) != 135:
        raise ValueError(f"SEO keyword catalog must contain 135 unique entries, found {len(entries)}")
    if len({_normalize(entry.get('keyword')) for entry in entries}) != len(entries):
        raise ValueError("SEO keyword catalog contains duplicate keywords")
    return payload


@lru_cache(maxsize=1)
def load_internal_link_catalog() -> Dict[str, Any]:
    with _INTERNAL_LINK_CATALOG_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    links = list(payload.get("links") or [])
    if len({str(link.get('id') or '') for link in links}) != len(links):
        raise ValueError("Internal-link catalog contains duplicate IDs")
    for link in links:
        if not str(link.get("url") or "").startswith("https://www.lippelift.de/"):
            raise ValueError(f"Internal link is outside the Lippe Lift allowlist: {link.get('url')}")
    return payload


def get_catalog_status() -> Dict[str, Any]:
    payload = load_keyword_catalog()
    source = dict(payload.get("source") or {})
    return {
        "enabled": seo_catalog_enabled(),
        "keyword_count": len(payload.get("entries") or []),
        "curated_row_count": int(source.get("curated_sheet_rows") or 0),
        "provider": source.get("provider") or "unknown",
        "metrics_as_of": source.get("metrics_as_of") or "unknown",
        "internal_link_count": len(load_internal_link_catalog().get("links") or []),
    }


def get_seo_seed_candidates() -> List[str]:
    """Return curated article titles in workbook priority order without duplicates."""
    candidates: List[str] = []
    seen: set[str] = set()
    entries = list(load_keyword_catalog().get("entries") or [])
    for entry in entries:
        titles = [
            str(row.get("title") or "").strip()
            for row in list(entry.get("curated_rows") or [])
            if str(row.get("title") or "").strip()
        ]
        for candidate in titles:
            signature = _normalize(candidate)
            if signature and signature not in seen:
                seen.add(signature)
                candidates.append(candidate)
    return candidates


def _entry_match_score(entry: Dict[str, Any], topic: str) -> tuple[int, int, int]:
    normalized_topic = _normalize(topic)
    keyword = _normalize(entry.get("keyword"))
    titles = [_normalize(row.get("title")) for row in list(entry.get("curated_rows") or [])]
    if normalized_topic == keyword:
        return (4, len(_tokens(keyword)), int(entry.get("search_volume") or 0))
    if normalized_topic in titles:
        return (3, len(_tokens(keyword)), int(entry.get("search_volume") or 0))
    topic_tokens = _tokens(topic)
    keyword_tokens = _tokens(keyword)
    overlap = sum(1 for token in keyword_tokens if _has_natural_token(topic_tokens, token))
    if overlap and overlap == len(keyword_tokens):
        return (2, overlap, int(entry.get("search_volume") or 0))
    return (0, overlap, int(entry.get("search_volume") or 0))


def find_keyword_entry(topic: str) -> Optional[Dict[str, Any]]:
    scored = [(_entry_match_score(entry, topic), entry) for entry in load_keyword_catalog().get("entries") or []]
    score, entry = max(scored, key=lambda pair: pair[0], default=((0, 0, 0), None))
    return dict(entry) if entry is not None and score[0] > 0 else None


def _secondary_keywords(entry: Dict[str, Any], limit: int = 6) -> List[str]:
    cluster = _normalize(entry.get("cluster"))
    primary = _normalize(entry.get("keyword"))
    related = [
        candidate
        for candidate in load_keyword_catalog().get("entries") or []
        if _normalize(candidate.get("cluster")) == cluster and _normalize(candidate.get("keyword")) != primary
    ]
    related.sort(key=lambda item: (-int(item.get("search_volume") or 0), str(item.get("keyword") or "")))
    return [str(item.get("keyword") or "").strip() for item in related[:limit]]


def _select_internal_links(topic: str, cluster: str, primary_keyword: str, limit: int = 3) -> List[Dict[str, str]]:
    context_tokens = _tokens(f"{topic} {cluster} {primary_keyword}")
    scored: List[tuple[int, Dict[str, Any]]] = []
    for link in load_internal_link_catalog().get("links") or []:
        tag_tokens = _tokens(" ".join(str(tag) for tag in list(link.get("tags") or [])))
        score = len(context_tokens & tag_tokens)
        if score:
            scored.append((score, link))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id") or "")))
    if not scored:
        scored = [(1, link) for link in load_internal_link_catalog().get("links") or [] if link.get("id") in {"blog", "products"}]
    return [
        {"id": str(link["id"]), "title": str(link["title"]), "url": str(link["url"])}
        for _, link in scored[:limit]
    ]


def build_seo_brief(topic: str) -> Dict[str, Any]:
    entry = find_keyword_entry(topic)
    if entry:
        primary_keyword = str(entry.get("keyword") or topic).strip()
        cluster = str(entry.get("cluster") or "").strip()
        intent = str(entry.get("search_intent") or "Information").strip()
        secondary = _secondary_keywords(entry)
        source_kind = "catalog"
        metrics = {
            "search_volume": int(entry.get("search_volume") or 0),
            "cpc_eur": entry.get("cpc_eur"),
            "competition": entry.get("competition"),
            "provider": load_keyword_catalog().get("source", {}).get("provider") or "unknown",
            "metrics_as_of": load_keyword_catalog().get("source", {}).get("metrics_as_of") or "unknown",
        }
    else:
        primary_keyword = derive_primary_keyword(topic)
        cluster = ""
        intent = "Information"
        secondary = []
        source_kind = "derived"
        metrics = None
    links = _select_internal_links(topic, cluster, primary_keyword)
    commercial = intent.lower() in {"transaktion", "vergleich/kommerziell"}
    return {
        "primary_keyword": primary_keyword,
        "secondary_keywords": secondary,
        "search_intent": intent,
        "target_audience": _DEFAULT_AUDIENCE,
        "internal_links": links,
        "cta": "Passende Liftlösung unverbindlich besprechen" if commercial else "Weiterführende Informationen bei LIPPE Lift lesen",
        "avoid_terms": list(_DEFAULT_AVOID_TERMS),
        "source_kind": source_kind,
        "cluster": cluster or None,
        "metrics": metrics,
    }


def get_enabled_seo_brief(topic: str) -> Optional[Dict[str, Any]]:
    return build_seo_brief(topic) if seo_catalog_enabled() else None


def format_seo_prompt_block(brief: Optional[Dict[str, Any]]) -> str:
    if not brief:
        return ""
    secondary = ", ".join(brief.get("secondary_keywords") or []) or "keine verbindlichen Nebenkeywords"
    links = "\n".join(
        f"- {link['id']}: {link['title']} ({link['url']})"
        for link in brief.get("internal_links") or []
    ) or "- keine passende Zielseite"
    avoid = ", ".join(brief.get("avoid_terms") or [])
    return (
        "SEO-DATEN:\n"
        f"Hauptkeyword: {brief.get('primary_keyword', '')}\n"
        f"Nebenkeywords: {secondary}\n"
        f"Suchintention: {brief.get('search_intent', '')}\n"
        f"Zielgruppe: {brief.get('target_audience', '')}\n"
        f"Interne Links / Zielseiten:\n{links}\n"
        f"Gewünschter CTA: {brief.get('cta', '')}\n"
        f"Zu vermeidende Begriffe oder Claims: {avoid}"
    )
