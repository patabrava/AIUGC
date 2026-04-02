"""
Static product knowledge parsing for Prompt 3.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional

from app.features.topics.schemas import ProductKnowledgeEntry


DEFAULT_PRODUCT_KNOWLEDGE_PATH = Path(__file__).resolve().parents[3] / "docs" / "Knowledge_Base_LippeLift.txt"
_PRODUCT_HEADER_RE = re.compile(
    r"^(?P<section>[A-D])\)\s+(?P<label>.*?)\s*\(Marketingname:\s*(?P<name>[^)]+)\)\s*$",
    re.MULTILINE,
)


def _clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _section_range(raw: str, match: re.Match[str], next_match: Optional[re.Match[str]]) -> str:
    start = match.start()
    end = next_match.start() if next_match is not None else len(raw)
    return raw[start:end].strip()


def _extract_support_facts(raw: str) -> List[str]:
    before_products = raw.split("2. PRODUKTE", 1)[0]
    support_facts: List[str] = []
    for line in before_products.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            item = _clean_line(stripped[2:])
            if item:
                support_facts.append(item)
    return support_facts[:8]


def _extract_product_facts(block: str) -> List[str]:
    facts: List[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            fact = _clean_line(stripped[2:])
            if fact and fact not in facts:
                facts.append(fact)
    return facts


def parse_product_knowledge_base(raw: str) -> List[ProductKnowledgeEntry]:
    support_facts = _extract_support_facts(raw)
    matches = list(_PRODUCT_HEADER_RE.finditer(raw))
    entries: List[ProductKnowledgeEntry] = []

    for index, match in enumerate(matches):
        next_match = matches[index + 1] if index + 1 < len(matches) else None
        block = _section_range(raw, match, next_match)
        source_label = _clean_line(match.group("label"))
        product_name = _clean_line(match.group("name"))
        facts = _extract_product_facts(block)[:12]
        if not facts:
            facts = [source_label]

        entries.append(
            ProductKnowledgeEntry(
                product_name=product_name,
                source_label=source_label,
                aliases=[product_name, source_label],
                summary=facts[0],
                facts=facts,
                support_facts=support_facts,
                is_active=product_name not in {"LL12", "Konstanz"},
            )
        )

    return [entry for entry in entries if entry.is_active]


@lru_cache(maxsize=4)
def load_product_knowledge_base(path_str: str, mtime_ns: int) -> List[ProductKnowledgeEntry]:
    raw = Path(path_str).read_text(encoding="utf-8")
    return parse_product_knowledge_base(raw)


def get_product_knowledge_base(path: Path = DEFAULT_PRODUCT_KNOWLEDGE_PATH) -> List[ProductKnowledgeEntry]:
    stat = path.stat()
    return load_product_knowledge_base(str(path), stat.st_mtime_ns)


def plan_product_mix(
    entries: Iterable[ProductKnowledgeEntry],
    count: int,
    seed: Optional[int] = None,
) -> List[ProductKnowledgeEntry]:
    del seed
    ordered = list(entries)
    if not ordered or count <= 0:
        return []
    planned: List[ProductKnowledgeEntry] = []
    index = 0
    while len(planned) < count:
        planned.append(ordered[index % len(ordered)])
        index += 1
    return planned
