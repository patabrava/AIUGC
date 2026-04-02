# Product Prompt 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated `Prompt 3` lane that generates product-only scripts from the static LippeLift knowledge base using plain-text Gemini responses and local parsing.

**Architecture:** Keep the existing three-lane topic system intact: `value` stays on `Prompt 1`, `lifestyle` stays on `Prompt 2`, and `product` gets a new `Prompt 3`. The implementation stays local to the topics slice: a static knowledge loader normalizes the built-in product file, `prompts.py` renders new `prompt3_*` text templates, `response_parsers.py` parses Gemini's plain-text response, `prompt3_runtime.py` retries and validates product generations, and `handlers.py` routes `post_type=product` into that lane without changing the downstream review flow.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, Jinja2, Gemini text generation via existing `LLMClient`, pytest

**Locality Budget:** `{files: 12 touched max including tests/templates, LOC/file: <=300 target and <=500 hard, deps: 0}`

**Spec:** `docs/superpowers/specs/2026-04-02-product-prompt3-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `app/features/topics/product_knowledge.py` | Parse and cache the static LippeLift knowledge base into active product records plus supporting company/service facts |
| `app/features/topics/prompt3_runtime.py` | Product-only text generation, retry loop, product validation, coverage/repeat planning |
| `app/features/topics/prompts.py` | `build_prompt3()` plus small formatting helpers for product facts |
| `app/features/topics/response_parsers.py` | `parse_prompt3_response()` for Gemini's plain-text `Produkt/Angle/Script/CTA/Fakten` format |
| `app/features/topics/schemas.py` | Pydantic contracts for normalized product knowledge and parsed Prompt 3 output |
| `app/features/topics/seed_builders.py` | `build_product_seed_payload()` for downstream post persistence |
| `app/features/topics/agents.py` | Facade exports for `generate_product_topics()` and `parse_prompt3_response()` |
| `app/features/topics/handlers.py` | Route `post_type=product` into Prompt 3 instead of Deep Research |
| `app/features/topics/prompt_data/prompt3_8s.txt` | 8-second product prompt template |
| `app/features/topics/prompt_data/prompt3_16s.txt` | 16-second product prompt template |
| `app/features/topics/prompt_data/prompt3_32s.txt` | 32-second product prompt template |
| `templates/batches/list.html` | Expose the `product` count input and include it in the temporary expected-posts total |
| `tests/test_product_prompt3.py` | Unit tests for product knowledge parsing, prompt parsing, and Prompt 3 runtime retry behavior |
| `tests/test_product_generation_regression.py` | Integration-style tests for product batch routing and post creation |
| `tests/test_topic_prompt_templates.py` | Prompt file existence tests and `build_prompt3()` template assertions |
| `tests/test_batches_status_progress.py` | HTML regression test for the visible `product` count field |

---

### Task 1: Add normalized product knowledge contracts and static loader

**Files:**
- Create: `app/features/topics/product_knowledge.py`
- Modify: `app/features/topics/schemas.py`
- Test: `tests/test_product_prompt3.py`

- [ ] **Step 1: Write the failing product knowledge tests**

Create `tests/test_product_prompt3.py` with:

```python
from app.features.topics.product_knowledge import parse_product_knowledge_base, plan_product_mix


SAMPLE_KNOWLEDGE_BASE = """
1. UNTERNEHMEN
- 100% Made in Germany
- 5 Jahre Gewaehrleistung auf den gesamten Lift

2. PRODUKTE (AKTIV IM SORTIMENT)
WICHTIG: LL12 und Konstanz werden NICHT mehr kommuniziert.
Aktive Produkte: T80 Plattform, Hublift STL300, Sitzlift T80, Sitzlift ST70

A) PLATTFORMTREPPENLIFT T80 (Marketingname:VARIO PLUS)
- Plattform oder Sitzlift auf derselben Schiene
- Tragfaehigkeit: 300 kg
- Innen- und Aussenbereich

B) HUBLIFT STL300 (Marketingname: LEVEL)
- Fuer Hoehen bis 2.990 mm
- Kein Aufzugsschacht erforderlich
- Tragfaehigkeit: 300 kg

C) SITZTREPPENLIFT T80 (Marketingname:VARIO ONE)
- Gerade und kurvige Treppen
- Austausch Sitz gegen Plattform nachtraeglich moeglich

D) SITZTREPPENLIFT ST70 - Der Klassiker (Marketingname: VIA)
- Speziell fuer kurvige, mehrstoeckige Treppen
- Mehrere Haltestellen moeglich
"""


def test_parse_product_knowledge_base_returns_only_active_products():
    entries = parse_product_knowledge_base(SAMPLE_KNOWLEDGE_BASE)
    assert [entry.product_name for entry in entries] == [
        "VARIO PLUS",
        "LEVEL",
        "VARIO ONE",
        "VIA",
    ]
    assert all(entry.is_active is True for entry in entries)


def test_parse_product_knowledge_base_attaches_support_facts_to_each_product():
    entries = parse_product_knowledge_base(SAMPLE_KNOWLEDGE_BASE)
    assert "100% Made in Germany" in entries[0].support_facts
    assert "5 Jahre Gewaehrleistung auf den gesamten Lift" in entries[1].support_facts


def test_plan_product_mix_covers_all_products_before_repeat():
    entries = parse_product_knowledge_base(SAMPLE_KNOWLEDGE_BASE)
    planned = plan_product_mix(entries, count=6)
    assert [entry.product_name for entry in planned[:4]] == [
        "VARIO PLUS",
        "LEVEL",
        "VARIO ONE",
        "VIA",
    ]
    assert len(planned) == 6
    assert planned[4].product_name == "VARIO PLUS"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_product_prompt3.py::test_parse_product_knowledge_base_returns_only_active_products tests/test_product_prompt3.py::test_parse_product_knowledge_base_attaches_support_facts_to_each_product tests/test_product_prompt3.py::test_plan_product_mix_covers_all_products_before_repeat -v`

Expected: FAIL with `ModuleNotFoundError` for `app.features.topics.product_knowledge` or missing imports from `schemas.py`

- [ ] **Step 3: Add the Pydantic contracts to `schemas.py`**

In `app/features/topics/schemas.py`, add:

```python
class ProductKnowledgeEntry(BaseModel):
    """Normalized active product facts derived from the static knowledge base."""
    product_name: str = Field(..., min_length=2, max_length=120)
    source_label: str = Field(..., min_length=2, max_length=200)
    aliases: List[str] = Field(default_factory=list, min_length=1, max_length=10)
    summary: str = Field(..., min_length=10, max_length=500)
    facts: List[str] = Field(default_factory=list, min_length=1, max_length=12)
    support_facts: List[str] = Field(default_factory=list, max_length=12)
    is_active: bool = True


class ProductPromptCandidate(BaseModel):
    """Plain-text Prompt 3 output after local parsing and validation."""
    product_name: str = Field(..., min_length=2, max_length=120)
    angle: str = Field(..., min_length=5, max_length=240)
    script: str = Field(..., min_length=10, max_length=900)
    cta: str = Field(..., min_length=2, max_length=240)
    facts: List[str] = Field(default_factory=list, min_length=1, max_length=5)
    framework: Literal["PAL", "Testimonial", "Transformation"] = "PAL"
    estimated_duration_s: int = Field(default=0, ge=0, le=32)

    @validator("product_name", "angle", "script", "cta")
    def _validate_product_prompt_text(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("Field cannot be empty")
        return cleaned
```

- [ ] **Step 4: Implement the static loader in `product_knowledge.py`**

Create `app/features/topics/product_knowledge.py` with:

```python
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from random import Random
from typing import Iterable, List, Optional

from app.features.topics.schemas import ProductKnowledgeEntry


DEFAULT_PRODUCT_KNOWLEDGE_PATH = Path(__file__).resolve().parents[3] / "docs" / "Knowledge_Base_LippeLift.txt"
_PRODUCT_HEADER_PATTERN = re.compile(r"^[A-D]\)\s+(.*?)\(Marketingname:\s*([^)]+)\)", re.MULTILINE)


def _clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _extract_support_facts(raw: str) -> List[str]:
    company_block = raw.split("2. PRODUKTE", 1)[0]
    return [
        _clean_line(line[2:])
        for line in company_block.splitlines()
        if line.strip().startswith("- ")
    ][:8]


def _extract_product_block(raw: str, header_match: re.Match[str], next_start: Optional[int]) -> str:
    start = header_match.start()
    end = next_start if next_start is not None else len(raw)
    return raw[start:end].strip()


def _extract_product_facts(block: str) -> List[str]:
    facts = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            facts.append(_clean_line(stripped[2:]))
    return facts


def parse_product_knowledge_base(raw: str) -> List[ProductKnowledgeEntry]:
    support_facts = _extract_support_facts(raw)
    matches = list(_PRODUCT_HEADER_PATTERN.finditer(raw))
    entries: List[ProductKnowledgeEntry] = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else None
        source_label = _clean_line(match.group(1))
        marketing_name = _clean_line(match.group(2))
        block = _extract_product_block(raw, match, next_start)
        facts = _extract_product_facts(block)
        entries.append(
            ProductKnowledgeEntry(
                product_name=marketing_name,
                source_label=source_label,
                aliases=[marketing_name, source_label],
                summary=facts[0] if facts else source_label,
                facts=facts,
                support_facts=support_facts,
                is_active=marketing_name not in {"LL12", "Konstanz"},
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


def plan_product_mix(entries: Iterable[ProductKnowledgeEntry], count: int, seed: Optional[int] = None) -> List[ProductKnowledgeEntry]:
    ordered = list(entries)
    if not ordered or count <= 0:
        return []
    rng = Random(seed)
    planned: List[ProductKnowledgeEntry] = []
    cycle = ordered[:]
    while len(planned) < count:
        if len(planned) and len(planned) % len(ordered) == 0:
            cycle = ordered[:]
            rng.shuffle(cycle)
        planned.append(cycle[len(planned) % len(cycle)])
    return planned
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_product_prompt3.py::test_parse_product_knowledge_base_returns_only_active_products tests/test_product_prompt3.py::test_parse_product_knowledge_base_attaches_support_facts_to_each_product tests/test_product_prompt3.py::test_plan_product_mix_covers_all_products_before_repeat -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/features/topics/schemas.py app/features/topics/product_knowledge.py tests/test_product_prompt3.py
git commit -m "feat: add static product knowledge loader for prompt 3"
```

---

### Task 2: Add `Prompt 3` templates and `build_prompt3()`

**Files:**
- Create: `app/features/topics/prompt_data/prompt3_8s.txt`
- Create: `app/features/topics/prompt_data/prompt3_16s.txt`
- Create: `app/features/topics/prompt_data/prompt3_32s.txt`
- Modify: `app/features/topics/prompts.py`
- Test: `tests/test_topic_prompt_templates.py`

- [ ] **Step 1: Write the failing prompt-template tests**

In `tests/test_topic_prompt_templates.py`, add:

```python
from app.features.topics.prompts import build_prompt3
from app.features.topics.schemas import ProductKnowledgeEntry


def _sample_product() -> ProductKnowledgeEntry:
    return ProductKnowledgeEntry(
        product_name="VARIO PLUS",
        source_label="PLATTFORMTREPPENLIFT T80",
        aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
        summary="Plattform oder Sitzlift auf derselben Schiene.",
        facts=[
            "Plattform oder Sitzlift auf derselben Schiene",
            "Tragfaehigkeit bis 300 kg",
            "Innen- und Aussenbereich",
        ],
        support_facts=[
            "100% Made in Germany",
            "5 Jahre Gewaehrleistung auf den gesamten Lift",
        ],
    )


def test_prompt_text_files_exist_for_all_duration_tiers():
    expected = {
        "prompt1_8s.txt",
        "prompt1_16s.txt",
        "prompt1_32s.txt",
        "prompt1_batch.txt",
        "prompt1_normalization.txt",
        "prompt2_8s.txt",
        "prompt2_16s.txt",
        "prompt2_32s.txt",
        "prompt3_8s.txt",
        "prompt3_16s.txt",
        "prompt3_32s.txt",
    }
    existing = {path.name for path in PROMPT_DATA_DIR.glob("prompt*.txt")}
    assert expected.issubset(existing)


def test_build_prompt3_uses_8s_text_template():
    prompt = build_prompt3(product=_sample_product(), profile=get_duration_profile(8))
    assert "8-Sekunden-UGC-Videos" in prompt
    assert "16-20 Woerter" in prompt
    assert "Antworte nicht in JSON" in prompt
    assert "Produkt: VARIO PLUS" in prompt
    assert "100% Made in Germany" in prompt


def test_build_prompt3_uses_32s_text_template():
    prompt = build_prompt3(product=_sample_product(), profile=get_duration_profile(32))
    assert "32-Sekunden-UGC-Videos" in prompt
    assert "40-66 Woerter" in prompt
    assert "5-6 Saetze" in prompt
    assert "LL12" in prompt
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_topic_prompt_templates.py::test_prompt_text_files_exist_for_all_duration_tiers tests/test_topic_prompt_templates.py::test_build_prompt3_uses_8s_text_template tests/test_topic_prompt_templates.py::test_build_prompt3_uses_32s_text_template -v`

Expected: FAIL because the `prompt3_*` files and `build_prompt3()` do not exist yet

- [ ] **Step 3: Create the three plain-text prompt templates**

Create `app/features/topics/prompt_data/prompt3_8s.txt`, `app/features/topics/prompt_data/prompt3_16s.txt`, and `app/features/topics/prompt_data/prompt3_32s.txt` with:

```text
--- app/features/topics/prompt_data/prompt3_8s.txt ---
Du schreibst deutsche Produkt-Skripte fuer 8-Sekunden-UGC-Videos auf TikTok und Instagram.

PRODUKT:
{product_name}
Quelle: {source_label}
Kurzkontext:
{product_summary}

HARTE REGELN:
- Antworte nicht in JSON.
- Antworte nur im exakt vorgegebenen Textformat.
- Nenne nur dieses Produkt und keine anderen Produkte.
- Nutze nur die Fakten aus diesem Prompt.
- Halte den Scripttext auf 16-20 Woerter und genau einen Satz.
- Du-Form, endkundentauglich, direkter Hook, kein LinkedIn-Ton.
- Keine Hashtags, keine Emojis, keine Aufzaehlungen im Script.
- Keine Aussagen zu LL12 oder Konstanz.

PRODUKTFAKTEN:
{product_facts}

STUETZFAKTEN:
{support_facts}

ANTWORTFORMAT:
Produkt: {product_name}
Angle: <ein klarer Produktwinkel fuer Endkunden>
Script: <ein finaler Ein-Satz-Scripttext>
CTA: <ein kurzer CTA>
Fakten:
- <Fakt 1>
- <Fakt 2>

--- app/features/topics/prompt_data/prompt3_16s.txt ---
Du schreibst deutsche Produkt-Skripte fuer 16-Sekunden-UGC-Videos auf TikTok und Instagram.

PRODUKT:
{product_name}
Quelle: {source_label}
Kurzkontext:
{product_summary}

HARTE REGELN:
- Antworte nicht in JSON.
- Antworte nur im exakt vorgegebenen Textformat.
- Nenne nur dieses Produkt und keine anderen Produkte.
- Nutze nur die Fakten aus diesem Prompt.
- Halte den Scripttext auf 24-34 Woerter und 3-4 Saetze.
- Du-Form, endkundentauglich, direkter Hook, kein LinkedIn-Ton.
- Keine Hashtags, keine Emojis, keine Aufzaehlungen im Script.
- Keine Aussagen zu LL12 oder Konstanz.

PRODUKTFAKTEN:
{product_facts}

STUETZFAKTEN:
{support_facts}

ANTWORTFORMAT:
Produkt: {product_name}
Angle: <ein klarer Produktwinkel fuer Endkunden>
Script: <ein finaler 3-4-Satz-Scripttext>
CTA: <ein kurzer CTA>
Fakten:
- <Fakt 1>
- <Fakt 2>

--- app/features/topics/prompt_data/prompt3_32s.txt ---
Du schreibst deutsche Produkt-Skripte fuer 32-Sekunden-UGC-Videos auf TikTok und Instagram.

PRODUKT:
{product_name}
Quelle: {source_label}
Kurzkontext:
{product_summary}

HARTE REGELN:
- Antworte nicht in JSON.
- Antworte nur im exakt vorgegebenen Textformat.
- Nenne nur dieses Produkt und keine anderen Produkte.
- Nutze nur die Fakten aus diesem Prompt.
- Halte den Scripttext auf 40-66 Woerter und 5-6 Saetze.
- Du-Form, endkundentauglich, direkter Hook, kein LinkedIn-Ton.
- Keine Hashtags, keine Emojis, keine Aufzaehlungen im Script.
- Keine Aussagen zu LL12 oder Konstanz.

PRODUKTFAKTEN:
{product_facts}

STUETZFAKTEN:
{support_facts}

ANTWORTFORMAT:
Produkt: {product_name}
Angle: <ein klarer Produktwinkel fuer Endkunden>
Script: <ein finaler 5-6-Satz-Scripttext>
CTA: <ein kurzer CTA>
Fakten:
- <Fakt 1>
- <Fakt 2>
```

- [ ] **Step 4: Add `build_prompt3()` to `prompts.py`**

In `app/features/topics/prompts.py`, add:

```python
from app.features.topics.schemas import ProductKnowledgeEntry, ResearchDossier


def _format_prompt3_fact_lines(values: List[str]) -> str:
    cleaned = [sanitize_metadata_text(value, max_sentences=2) for value in values if str(value or "").strip()]
    if not cleaned:
        return "- Keine Zusatzfakten vorhanden."
    return "\n".join(f"- {item}" for item in cleaned[:8])


def build_prompt3(
    *,
    product: ProductKnowledgeEntry | Dict[str, Any],
    profile: Optional[DurationProfile] = None,
) -> str:
    profile = profile or get_duration_profile(8)
    payload = product.model_dump(mode="json") if isinstance(product, ProductKnowledgeEntry) else dict(product)
    template = _load_text_prompt("prompt3", profile.target_length_tier)
    return template.format(
        product_name=str(payload.get("product_name") or "").strip(),
        source_label=str(payload.get("source_label") or "").strip(),
        product_summary=_clip_text(payload.get("summary") or "", 320),
        product_facts=_format_prompt3_fact_lines(list(payload.get("facts") or [])),
        support_facts=_format_prompt3_fact_lines(list(payload.get("support_facts") or [])),
    ).strip()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_topic_prompt_templates.py::test_prompt_text_files_exist_for_all_duration_tiers tests/test_topic_prompt_templates.py::test_build_prompt3_uses_8s_text_template tests/test_topic_prompt_templates.py::test_build_prompt3_uses_32s_text_template -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/features/topics/prompts.py app/features/topics/prompt_data/prompt3_8s.txt app/features/topics/prompt_data/prompt3_16s.txt app/features/topics/prompt_data/prompt3_32s.txt tests/test_topic_prompt_templates.py
git commit -m "feat: add prompt 3 templates for product scripts"
```

---

### Task 3: Parse plain-text Prompt 3 output and add the retrying product runtime

**Files:**
- Create: `app/features/topics/prompt3_runtime.py`
- Modify: `app/features/topics/response_parsers.py`
- Modify: `app/features/topics/agents.py`
- Test: `tests/test_product_prompt3.py`

- [ ] **Step 1: Extend the unit tests for plain-text parsing and runtime retries**

In `tests/test_product_prompt3.py`, append:

```python
from app.features.topics.response_parsers import parse_prompt3_response
from app.features.topics.prompt3_runtime import generate_product_topics


def test_parse_prompt3_response_reads_plain_text_blocks():
    raw = """Produkt: VARIO PLUS
Angle: Eine Schiene fuer heute und spaeter
Script: Kennst du eine Treppe, die heute geht und morgen ploetzlich nicht mehr sicher ist?
CTA: Lass dir zeigen, wie eine Schiene beide Wege offen haelt.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
- Tragfaehigkeit bis 300 kg
"""
    candidate = parse_prompt3_response(raw)
    assert candidate.product_name == "VARIO PLUS"
    assert candidate.angle.startswith("Eine Schiene")
    assert candidate.script.endswith("?")
    assert candidate.cta.startswith("Lass dir zeigen")
    assert candidate.facts[0].startswith("Plattform")


def test_parse_prompt3_response_rejects_missing_required_fields():
    raw = """Produkt: VARIO PLUS
Script: Fehlendes Feldformat.
CTA: Mehr erfahren.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
"""
    with pytest.raises(ValidationError):
        parse_prompt3_response(raw)


class _FakeProductLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.text_prompts = []

    def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
        self.text_prompts.append((prompt, system_prompt, kwargs))
        return self.responses.pop(0)


def test_generate_product_topics_retries_when_wrong_product_is_returned(monkeypatch):
    from app.features.topics.schemas import ProductKnowledgeEntry

    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="VARIO PLUS",
                source_label="PLATTFORMTREPPENLIFT T80",
                aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
                summary="Plattform oder Sitzlift auf derselben Schiene.",
                facts=["Plattform oder Sitzlift auf derselben Schiene", "Tragfaehigkeit bis 300 kg"],
                support_facts=["100% Made in Germany"],
            )
        ],
    )
    fake_llm = _FakeProductLLM(
        [
            """Produkt: LL12
Angle: Falsches Produkt
Script: Dieses Produkt sollte hier gar nicht auftauchen.
CTA: Nicht verwenden.
Fakten:
- Falscher Fakt
""",
            """Produkt: VARIO PLUS
Angle: Eine Schiene fuer heute und spaeter
Script: Kennst du eine Treppe, die heute geht und morgen ploetzlich nicht mehr sicher ist?
CTA: Lass dir zeigen, wie eine Schiene beide Wege offen haelt.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
- Tragfaehigkeit bis 300 kg
""",
        ]
    )

    generated = generate_product_topics(
        count=1,
        target_length_tier=8,
        llm_factory=lambda: fake_llm,
    )

    assert generated[0]["product_name"] == "VARIO PLUS"
    assert len(fake_llm.text_prompts) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_product_prompt3.py::test_parse_prompt3_response_reads_plain_text_blocks tests/test_product_prompt3.py::test_parse_prompt3_response_rejects_missing_required_fields tests/test_product_prompt3.py::test_generate_product_topics_retries_when_wrong_product_is_returned -v`

Expected: FAIL because `parse_prompt3_response()` and `generate_product_topics()` do not exist yet

- [ ] **Step 3: Add `parse_prompt3_response()` to `response_parsers.py`**

In `app/features/topics/response_parsers.py`, add:

```python
def parse_prompt3_response(raw: str) -> ProductPromptCandidate:
    fields: Dict[str, str] = {}
    facts: List[str] = []
    in_facts = False

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("fakten:"):
            in_facts = True
            continue
        if in_facts and line.startswith("-"):
            facts.append(line[1:].strip())
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            normalized_key = key.strip().lower()
            if normalized_key in {"produkt", "angle", "script", "cta"}:
                fields[normalized_key] = value.strip()
                in_facts = False
                continue
        if in_facts:
            facts.append(line)

    if not {"produkt", "angle", "script", "cta"}.issubset(fields):
        raise ValidationError(
            message="PROMPT_3 output missing required fields",
            details={"fields": sorted(fields.keys())},
        )

    return ProductPromptCandidate(
        product_name=fields["produkt"],
        angle=fields["angle"],
        script=fields["script"],
        cta=fields["cta"],
        facts=[fact for fact in facts if fact][:5],
        estimated_duration_s=estimate_script_duration_seconds(fields["script"]),
    )
```

- [ ] **Step 4: Add the runtime and facade export**

Create `app/features/topics/prompt3_runtime.py` with:

```python
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional

from app.adapters.llm_client import get_llm_client
from app.core.errors import ValidationError
from app.core.video_profiles import get_duration_profile
from app.features.topics.content_utils import strip_cta_from_script
from app.features.topics.product_knowledge import get_product_knowledge_base, plan_product_mix
from app.features.topics.prompts import build_prompt3
from app.features.topics.response_parsers import parse_prompt3_response


def _normalize_alias(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _candidate_matches_entry(candidate, entry) -> bool:
    aliases = {_normalize_alias(alias) for alias in entry.aliases}
    aliases.add(_normalize_alias(entry.product_name))
    return _normalize_alias(candidate.product_name) in aliases


def generate_product_topics(
    *,
    count: int = 1,
    seed: Optional[int] = None,
    target_length_tier: Optional[int] = None,
    llm_factory: Callable = get_llm_client,
) -> List[Dict[str, object]]:
    llm = llm_factory()
    profile = get_duration_profile(target_length_tier or 8)
    entries = get_product_knowledge_base()
    planned_entries = plan_product_mix(entries, count=count, seed=seed)
    results: List[Dict[str, object]] = []

    for entry in planned_entries:
        prompt = build_prompt3(product=entry, profile=profile)
        last_error = ""
        for attempt in range(3):
            response_text = llm.generate_gemini_text(
                prompt=prompt,
                system_prompt=None,
                max_tokens=1200,
            )
            candidate = parse_prompt3_response(response_text)
            if not _candidate_matches_entry(candidate, entry):
                last_error = f"Falsches Produkt genannt: {candidate.product_name}"
                prompt = f"{prompt}\n\nFEEDBACK: {last_error}. Nenne nur {entry.product_name}."
                continue
            rotation = strip_cta_from_script(candidate.script, candidate.cta) or candidate.script.strip()
            results.append(
                {
                    "title": f"{entry.product_name}: {candidate.angle}",
                    "rotation": rotation,
                    "cta": candidate.cta,
                    "spoken_duration": max(1, int(candidate.estimated_duration_s or math.ceil(len(candidate.script.split()) / 2.6))),
                    "script": candidate.script,
                    "framework": candidate.framework,
                    "product_name": entry.product_name,
                    "angle": candidate.angle,
                    "facts": candidate.facts,
                    "source_summary": entry.summary,
                    "support_facts": entry.support_facts,
                }
            )
            break
        else:
            raise ValidationError(
                message="PROMPT_3 generation failed after text normalization",
                details={"product_name": entry.product_name, "target_length_tier": profile.target_length_tier, "last_error": last_error},
            )
    return results
```

In `app/features/topics/agents.py`, add:

```python
from app.features.topics.prompt3_runtime import generate_product_topics as _generate_product_topics
from app.features.topics.response_parsers import parse_prompt2_response, parse_prompt3_response


def generate_product_topics(count: int = 1, seed: Optional[int] = None, target_length_tier: Optional[int] = None) -> List[Dict[str, object]]:
    return _generate_product_topics(
        count=count,
        seed=seed,
        target_length_tier=target_length_tier,
        llm_factory=get_llm_client,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_product_prompt3.py::test_parse_prompt3_response_reads_plain_text_blocks tests/test_product_prompt3.py::test_parse_prompt3_response_rejects_missing_required_fields tests/test_product_prompt3.py::test_generate_product_topics_retries_when_wrong_product_is_returned -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/features/topics/response_parsers.py app/features/topics/prompt3_runtime.py app/features/topics/agents.py tests/test_product_prompt3.py
git commit -m "feat: add prompt 3 parser and runtime"
```

---

### Task 4: Build product seed payloads and route `post_type=product` into Prompt 3

**Files:**
- Modify: `app/features/topics/seed_builders.py`
- Modify: `app/features/topics/handlers.py`
- Test: `tests/test_product_generation_regression.py`

- [ ] **Step 1: Write the failing product routing tests**

Create `tests/test_product_generation_regression.py` with:

```python
from __future__ import annotations

import app.features.topics.handlers as topic_handlers
from app.features.topics.seed_builders import build_product_seed_payload


def test_build_product_seed_payload_keeps_product_context():
    payload = build_product_seed_payload(
        {
            "title": "VARIO PLUS: Eine Schiene fuer heute und spaeter",
            "rotation": "Kennst du eine Treppe, die heute geht und morgen ploetzlich nicht mehr sicher ist?",
            "cta": "Lass dir zeigen, wie eine Schiene beide Wege offen haelt.",
            "spoken_duration": 6,
            "script": "Kennst du eine Treppe, die heute geht und morgen ploetzlich nicht mehr sicher ist?",
            "framework": "PAL",
            "product_name": "VARIO PLUS",
            "angle": "Eine Schiene fuer heute und spaeter",
            "facts": [
                "Plattform oder Sitzlift auf derselben Schiene",
                "Tragfaehigkeit bis 300 kg",
            ],
            "source_summary": "Plattform oder Sitzlift auf derselben Schiene.",
            "support_facts": ["100% Made in Germany"],
        }
    )

    assert payload["canonical_topic"] == "VARIO PLUS"
    assert payload["product_name"] == "VARIO PLUS"
    assert payload["product_angle"] == "Eine Schiene fuer heute und spaeter"
    assert payload["strict_seed"]["facts"][0] == "Plattform oder Sitzlift auf derselben Schiene"


def test_discover_topics_routes_product_batches_to_prompt3(monkeypatch):
    created_posts = []
    batch = {
        "id": "batch-product",
        "brand": "Product Fixture",
        "state": "S1_SETUP",
        "post_type_counts": {"value": 0, "lifestyle": 0, "product": 2},
        "target_length_tier": 8,
    }

    generated_topics = [
        {
            "title": "VARIO PLUS: Eine Schiene fuer heute und spaeter",
            "rotation": "Kennst du eine Treppe, die heute geht und morgen ploetzlich nicht mehr sicher ist?",
            "cta": "Lass dir zeigen, wie eine Schiene beide Wege offen haelt.",
            "spoken_duration": 6,
            "script": "Kennst du eine Treppe, die heute geht und morgen ploetzlich nicht mehr sicher ist?",
            "framework": "PAL",
            "product_name": "VARIO PLUS",
            "angle": "Eine Schiene fuer heute und spaeter",
            "facts": ["Plattform oder Sitzlift auf derselben Schiene"],
            "source_summary": "Plattform oder Sitzlift auf derselben Schiene.",
            "support_facts": ["100% Made in Germany"],
        },
        {
            "title": "LEVEL: Ohne Schacht nach oben",
            "rotation": "Schon ein kleiner Hoehenunterschied blockiert dich jeden Tag, obwohl dafuer kein Aufzugsschacht noetig waere.",
            "cta": "Frag nach, wie LEVEL kurze Hoehen sauber ueberbrueckt.",
            "spoken_duration": 6,
            "script": "Schon ein kleiner Hoehenunterschied blockiert dich jeden Tag, obwohl dafuer kein Aufzugsschacht noetig waere.",
            "framework": "PAL",
            "product_name": "LEVEL",
            "angle": "Ohne Schacht nach oben",
            "facts": ["Kein Aufzugsschacht erforderlich"],
            "source_summary": "Kein Aufzugsschacht erforderlich.",
            "support_facts": ["100% Made in Germany"],
        },
    ]

    monkeypatch.setattr(topic_handlers, "get_batch_by_id", lambda batch_id: dict(batch))
    monkeypatch.setattr(topic_handlers, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(topic_handlers, "generate_product_topics", lambda count=1, seed=None, target_length_tier=None: generated_topics[:count])
    monkeypatch.setattr(topic_handlers, "generate_topics_research_agent", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Prompt 1 must not run for product batches")))
    monkeypatch.setattr(topic_handlers, "add_topic_to_registry", lambda **kwargs: {"id": "topic-registry-id"})
    monkeypatch.setattr(topic_handlers, "_attach_publish_captions", lambda **kwargs: dict(kwargs["seed_payload"], caption=f"Caption for {kwargs['topic_title']}"))
    monkeypatch.setattr(topic_handlers, "update_batch_state", lambda batch_id, target_state: {"id": batch_id, "state": getattr(target_state, "value", target_state)})

    def _fake_create_post_for_batch(**kwargs):
        created_posts.append(kwargs)
        return {"id": f"post-{len(created_posts)}", **kwargs}

    monkeypatch.setattr(topic_handlers, "create_post_for_batch", _fake_create_post_for_batch)
    topic_handlers.clear_seeding_progress(batch["id"])

    result = topic_handlers._discover_topics_for_batch_sync(batch["id"])

    assert result["posts_created"] == 2
    assert {post["post_type"] for post in created_posts} == {"product"}
    assert created_posts[0]["seed_data"]["product_name"] == "VARIO PLUS"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_product_generation_regression.py::test_build_product_seed_payload_keeps_product_context tests/test_product_generation_regression.py::test_discover_topics_routes_product_batches_to_prompt3 -v`

Expected: FAIL because `build_product_seed_payload()` and the `product` branch do not exist yet

- [ ] **Step 3: Add `build_product_seed_payload()`**

In `app/features/topics/seed_builders.py`, add:

```python
def build_product_seed_payload(topic_data: Dict[str, Any]) -> Dict[str, Any]:
    script = str(topic_data.get("script") or topic_data.get("rotation") or "").strip()
    facts = [str(item).strip() for item in list(topic_data.get("facts") or []) if str(item).strip()]
    source_summary = str(topic_data.get("source_summary") or "").strip()
    strict_seed = {
        "facts": facts[:5] or [str(topic_data.get("title") or "").strip()],
        "source_context": source_summary or None,
    }
    return {
        "script": script,
        "canonical_topic": str(topic_data.get("product_name") or topic_data.get("title") or "").strip(),
        "research_title": str(topic_data.get("title") or "").strip(),
        "framework": str(topic_data.get("framework") or "PAL"),
        "tone": "direkt, freundlich, empowernd, du-Form",
        "estimated_duration_s": int(topic_data.get("spoken_duration") or 0),
        "cta": str(topic_data.get("cta") or "").strip(),
        "dialog_script": script,
        "script_category": "problem",
        "strict_fact": strict_seed["facts"][0],
        "strict_seed": strict_seed,
        "description": build_social_description(script, source_summary),
        "disclaimer": "Keine Rechts- oder medizinische Beratung.",
        "product_name": str(topic_data.get("product_name") or "").strip(),
        "product_angle": str(topic_data.get("angle") or "").strip(),
        "support_facts": list(topic_data.get("support_facts") or []),
    }
```

- [ ] **Step 4: Route `product` through Prompt 3 in `handlers.py`**

In `app/features/topics/handlers.py`, update imports and add a dedicated `product` branch:

```python
from app.features.topics.agents import (
    generate_lifestyle_topics,
    generate_product_topics,
    build_lifestyle_seed_payload,
)
from app.features.topics.seed_builders import build_product_seed_payload, build_research_seed_data


# Replace the old comment:
# lifestyle uses PROMPT_2 direct; value/product use Deep Research via PROMPT_1.
# With:
# lifestyle uses PROMPT_2 direct; product uses PROMPT_3 direct; value uses Deep Research via PROMPT_1.

        if post_type == "lifestyle":
            ...
        elif post_type == "product":
            product_topics = generate_product_topics(
                count=count,
                target_length_tier=resolved_target_tier,
            )
            for topic_data in product_topics[:count]:
                seed_payload = build_product_seed_payload(topic_data)
                seed_payload = _attach_publish_captions(
                    topic_title=topic_data["title"],
                    post_type=post_type,
                    seed_payload=seed_payload,
                    script_fallback=topic_data["rotation"],
                    canonical_topic=str(seed_payload.get("canonical_topic") or topic_data["product_name"]),
                )
                add_topic_to_registry(
                    title=topic_data["title"],
                    script=topic_data["rotation"],
                    post_type=post_type,
                    canonical_topic=str(seed_payload.get("canonical_topic") or topic_data["product_name"]),
                )
                post = create_post_for_batch(
                    batch_id=batch_id,
                    post_type=post_type,
                    topic_title=topic_data["title"],
                    topic_rotation=topic_data["rotation"],
                    topic_cta=topic_data["cta"],
                    spoken_duration=float(topic_data["spoken_duration"]),
                    seed_data=seed_payload,
                    target_length_tier=resolved_target_tier,
                )
                created_posts.append(post)
                all_generated_topics.append(
                    {
                        "title": topic_data["title"],
                        "rotation": topic_data["rotation"],
                        "cta": topic_data["cta"],
                        "spoken_duration": float(topic_data["spoken_duration"]),
                        "seed_payload": {"canonical_topic": seed_payload["canonical_topic"]},
                    }
                )
        else:
            # existing value / Prompt 1 path
            ...
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_product_generation_regression.py::test_build_product_seed_payload_keeps_product_context tests/test_product_generation_regression.py::test_discover_topics_routes_product_batches_to_prompt3 -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/features/topics/seed_builders.py app/features/topics/handlers.py tests/test_product_generation_regression.py
git commit -m "feat: route product batches through prompt 3"
```

---

### Task 5: Expose `product` in the batch create modal and verify the visible HTML

**Files:**
- Modify: `templates/batches/list.html`
- Test: `tests/test_batches_status_progress.py`

- [ ] **Step 1: Write the failing HTML regression test**

In `tests/test_batches_status_progress.py`, add:

```python
def test_batches_list_modal_includes_product_count_input(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(batch_handlers, "list_batches", lambda archived=None, limit=50, offset=0: ([], 0))

    response = client.get("/batches", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert 'name="post_type_counts.product"' in response.text
    assert "expected: value + lifestyle + product" in response.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_batches_status_progress.py::test_batches_list_modal_includes_product_count_input -v`

Expected: FAIL because the modal does not render the `product` field and the Alpine expected-posts sum still omits it

- [ ] **Step 3: Update the batch create modal**

In `templates/batches/list.html`, change the `@htmx:before-request` block and add a third count row:

```html
@htmx:before-request="
    const brand = $event.detail.elt.querySelector('[name=brand]').value;
    const value = parseInt($event.detail.elt.querySelector('[name=\'post_type_counts.value\']').value) || 0;
    const lifestyle = parseInt($event.detail.elt.querySelector('[name=\'post_type_counts.lifestyle\']').value) || 0;
    const product = parseInt($event.detail.elt.querySelector('[name=\'post_type_counts.product\']').value) || 0;
    const targetLengthTier = parseInt($event.detail.elt.querySelector('[name=target_length_tier]').value) || 8;
    const tempId = 'temp-' + Date.now();
    window.dispatchEvent(new CustomEvent('batch-progress:update', {
        detail: {
            batchId: tempId,
            brand,
            expected: value + lifestyle + product,
            targetLengthTier,
            posts: 0,
            isTemp: true,
        }
    }));
"
```

Add the missing field:

```html
<div class="flex items-center justify-between">
    <label class="text-sm text-gray-600">Product Posts</label>
    <input
        type="number"
        name="post_type_counts.product"
        min="0"
        max="100"
        value="0"
        class="w-20 border border-gray-300 rounded-md py-1 px-2 text-center"
    >
</div>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_batches_status_progress.py::test_batches_list_modal_includes_product_count_input -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add templates/batches/list.html tests/test_batches_status_progress.py
git commit -m "feat: expose product counts in batch create modal"
```

---

### Task 6: Run the focused regression sweep and close the loop

**Files:**
- Modify: none
- Test: `tests/test_product_prompt3.py`
- Test: `tests/test_product_generation_regression.py`
- Test: `tests/test_topic_prompt_templates.py`
- Test: `tests/test_batches_status_progress.py`

- [ ] **Step 1: Run the full focused product suite**

Run: `pytest tests/test_product_prompt3.py tests/test_product_generation_regression.py tests/test_topic_prompt_templates.py tests/test_batches_status_progress.py -v`

Expected: PASS

- [ ] **Step 2: Run the existing lifestyle and Gemini regressions to catch accidental lane bleed**

Run: `pytest tests/test_lifestyle_generation_regression.py tests/test_topics_gemini_flow.py -v`

Expected: PASS

- [ ] **Step 3: If any failure shows value/product lane bleed, fix the smallest boundary first**

Apply fixes in this order only:

```text
1. Facade exports in app/features/topics/agents.py
2. Prompt builder or parser in app/features/topics/prompts.py / response_parsers.py
3. Product runtime in app/features/topics/prompt3_runtime.py
4. Handler routing in app/features/topics/handlers.py
```

- [ ] **Step 4: Commit the verification pass**

If Steps 1-3 required follow-up code changes, commit them with:

```bash
git add app/features/topics app/features/batches templates/batches tests
git commit -m "test: verify product prompt 3 integration"
```

---

## Self-Review

- **Spec coverage:** Task 1 covers the static knowledge source and caching shape. Task 2 covers the dedicated Prompt 3 templates and non-JSON contract. Task 3 covers plain-text parsing, validation, and retry behavior. Task 4 covers batch routing, post creation, and seed payload shaping. Task 5 covers the visible batch form requirement. Task 6 covers regression protection against value/lifestyle breakage. No spec gaps remain.
- **Placeholder scan:** No `TBD`, `TODO`, or “similar to” references remain. Every task includes explicit files, code snippets, commands, and expected outcomes.
- **Type consistency:** The plan consistently uses `ProductKnowledgeEntry`, `ProductPromptCandidate`, `build_prompt3()`, `parse_prompt3_response()`, `generate_product_topics()`, and `build_product_seed_payload()` across all tasks.
