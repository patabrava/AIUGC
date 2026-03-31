# Caption Generation Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the generic 3-format caption system with a research-driven Hook→Body→CTA structure that generates 3 emotional-angle variations using actual research facts.

**Architecture:** Rewrite `captions.py` to remove ~300 lines of fallback/family machinery. New prompt passes script hook + research facts explicitly. LLM generates 3 variations (curiosity/personal/provocative) on a single proven structure. Simplified validation checks length, CTA presence, script overlap, and topical alignment.

**Tech Stack:** Python 3.11, FastAPI, Gemini LLM via `llm_client.py`, Pydantic, pytest

**Spec:** `docs/superpowers/specs/2026-03-30-caption-generation-redesign.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `app/features/topics/captions.py` | Major rewrite | Caption generation, validation, variant selection |
| `app/features/topics/prompt_data/captions_prompt.txt` | Replace | New Hook→Body→CTA prompt template |
| `app/features/topics/handlers.py:73-89` | Modify | Pass `research_facts` and `script_hook` to caption generator |
| `app/features/topics/seed_builders.py` | Minor modify | Expose raw research facts without over-sanitization |
| `tests/test_caption_generation.py` | Rewrite | New tests for Hook→Body→CTA structure |

---

### Task 1: Replace the Prompt Template

**Files:**
- Replace: `app/features/topics/prompt_data/captions_prompt.txt`

- [ ] **Step 1: Read the current prompt template**

Read `app/features/topics/prompt_data/captions_prompt.txt` to confirm the current content matches what we expect (3-format marker system with `[short_paragraph]`, `[medium_bullets]`, `[long_structured]`).

- [ ] **Step 2: Write the new prompt template**

Replace the entire file with:

```text
Du bekommst ein Thema, ein Skript, den Skript-Hook und Recherche-Fakten.
Erstelle exakt 3 Caption-Varianten mit UNTERSCHIEDLICHEN emotionalen Winkeln.
Jede Caption folgt der gleichen Struktur: Hook, Body, CTA, Hashtags.

SKRIPT-HOOK (das sagt das Video am Anfang — NICHT wiederholen):
{script_hook}

RECHERCHE-FAKTEN (waehle die ueberraschendsten fuer den Body):
{research_facts}

THEMA: {topic_title}
POST-TYP: {post_type}
SKRIPT: {script}

BEISPIEL-OUTPUT (so muss dein Output aussehen — alle 3 Marker, alle 3 Captions):

[curiosity]
Das sagt dir dein Verkehrsbetrieb nicht: Viele Kommunen nutzten jahrelang Ausnahmen, um Barrierefreiheit zu umgehen. Ab 2026 ist damit Schluss.

Speicher dir das, bevor es untergeht.

#BarriereFreiheit #ÖPNV #Inklusion

[personal]
Wenn du ÖPNV faehrst, betrifft dich das ab 2026. Viele Haltestellen sind bis heute nicht barrierefrei, obwohl das Gesetz es laengst verlangt.

Schick das jemandem, der das wissen muss.

#Barrierefrei #Alltag #Selbstbestimmt

[provocative]
Keine Rampe, kein Aufzug, keine Ansage — und trotzdem nennen sie es barrierefrei. Ab 2026 gibt es fuer Kommunen keine Ausreden mehr.

Kommentier, wenn deine Haltestelle betroffen ist.

#BarriereFreiheit #ÖPNV #Teilhabe

JETZT BIST DU DRAN. Erstelle 3 Varianten fuer dieses Thema.

Struktur JEDER Caption:
- HOOK: Max 100 Zeichen. Hauptkeyword in den ersten 30 Zeichen. Anderer emotionaler Winkel als der Skript-Hook. Nie den Skript-Hook woertlich kopieren.
- BODY: 1-2 Saetze, max 150 Zeichen. Nutze einen KONKRETEN Fakt aus den Recherche-Fakten.
- CTA: Direkte Handlungsaufforderung (Speichern, Teilen oder Kommentieren). Immer eine eigene Zeile.
- HASHTAGS: 3-5 thematische Hashtags in CamelCase. Eigene Zeile.

Emotionale Winkel:
- [curiosity]: Neugier oder Social-Proof (Was verschwiegen wird, was wenige wissen, ueberraschende Zahlen)
- [personal]: Persoenliche Betroffenheit (Du-Ansprache, Alltagsbezug, direkte Konsequenz)
- [provocative]: Herausforderung oder Widerspruch (Kritik, Gegensatz, Forderung)

Regeln:
- Jeder Hook muss einen ANDEREN emotionalen Winkel haben als die anderen zwei.
- Body muss einen KONKRETEN Fakt aus den Recherche-Fakten enthalten — keine generischen Saetze.
- Gesamtlaenge pro Caption (inkl. Hook, Body, CTA, Hashtags): 150-300 Zeichen.
- Alles auf Deutsch, natuerlich gesprochen, keine Uebersetzungsartefakte.
- Max 1 Emoji pro Caption.
- Schreibe alle 3 Varianten mit den Markern [curiosity], [personal] und [provocative].
```

- [ ] **Step 3: Verify the file was written correctly**

Read the file back and confirm all 4 template variables are present: `{script_hook}`, `{research_facts}`, `{topic_title}`, `{post_type}`, `{script}`.

- [ ] **Step 4: Commit**

```bash
git add app/features/topics/prompt_data/captions_prompt.txt
git commit -m "feat(captions): replace 3-format prompt with Hook-Body-CTA template

New prompt generates 3 emotional-angle variations (curiosity/personal/provocative)
using research facts and complementary hooks instead of format variants."
```

---

### Task 2: Rewrite Core Caption Generation in captions.py

**Files:**
- Modify: `app/features/topics/captions.py`
- Test: `tests/test_caption_generation.py`

- [ ] **Step 1: Write failing tests for the new caption structure**

Replace the entire contents of `tests/test_caption_generation.py` with:

```python
from copy import deepcopy

import pytest

from app.core.errors import ValidationError
from app.features.publish import handlers as publish_handlers
from app.features.topics import captions


CURIOSITY_BODY = (
    "Das sagt dir dein Verkehrsbetrieb nicht: "
    "Viele Kommunen nutzten Ausnahmen, um Barrierefreiheit zu umgehen. "
    "Ab 2026 ist Schluss.\n\n"
    "Speicher dir das.\n\n"
    "#BarriereFreiheit #ÖPNV #Inklusion"
)

PERSONAL_BODY = (
    "Wenn du ÖPNV fährst, betrifft dich das ab 2026. "
    "Viele Haltestellen sind bis heute nicht barrierefrei.\n\n"
    "Schick das jemandem, der das wissen muss.\n\n"
    "#Barrierefrei #Alltag #Selbstbestimmt"
)

PROVOCATIVE_BODY = (
    "Keine Rampe, kein Aufzug — und trotzdem barrierefrei? "
    "Ab 2026 gibt es für Kommunen keine Ausreden mehr.\n\n"
    "Kommentier, wenn deine Haltestelle betroffen ist.\n\n"
    "#BarriereFreiheit #ÖPNV #Teilhabe"
)

VARIANT_KEYS = ("curiosity", "personal", "provocative")


class _StubLLM:
    def __init__(self, payload):
        self.payload = payload

    def generate_gemini_text(self, **_kwargs):
        variants = self.payload.get("variants", [])
        parts = []
        for v in variants:
            parts.append(f"[{v['key']}]")
            parts.append(v["body"])
            parts.append("")
        return "\n".join(parts)


def _make_stub_llm():
    return _StubLLM(
        {
            "variants": [
                {"key": "curiosity", "body": CURIOSITY_BODY},
                {"key": "personal", "body": PERSONAL_BODY},
                {"key": "provocative", "body": PROVOCATIVE_BODY},
            ]
        }
    )


# --- Validation ---


@pytest.mark.parametrize(
    ("key", "body"),
    [
        ("curiosity", CURIOSITY_BODY),
        ("personal", PERSONAL_BODY),
        ("provocative", PROVOCATIVE_BODY),
    ],
)
def test_validate_caption_variant_accepts_new_structure(key, body):
    result = captions.validate_caption_variant(
        key, body, "Ein ganz anderes Skript das nichts mit der Caption zu tun hat."
    )
    assert result["key"] == key
    assert 150 <= result["char_count"] <= 300


def test_validate_caption_variant_rejects_unknown_key():
    with pytest.raises(ValidationError, match="Unknown caption family"):
        captions.validate_caption_variant(
            "short_paragraph", CURIOSITY_BODY, "Ein anderes Skript."
        )


def test_validate_caption_variant_rejects_too_short():
    with pytest.raises(ValidationError, match="target length"):
        captions.validate_caption_variant(
            "curiosity", "Zu kurz. #Tag", "Ein anderes Skript."
        )


def test_validate_caption_variant_rejects_too_long():
    long = "A" * 301 + "\n\nSpeicher dir das.\n\n#Tag1 #Tag2"
    with pytest.raises(ValidationError, match="target length"):
        captions.validate_caption_variant("curiosity", long, "Ein anderes Skript.")


def test_validate_caption_variant_rejects_more_than_one_emoji():
    body = (
        "Das wissen die wenigsten ✨ 🚦 über Barrierefreiheit im ÖPNV. "
        "Viele Haltestellen sind nicht barrierefrei.\n\n"
        "Speicher dir das.\n\n"
        "#Barrierefrei #ÖPNV"
    )
    with pytest.raises(ValidationError):
        captions.validate_caption_variant("curiosity", body, "Ein anderes Skript.")


def test_validate_caption_variant_rejects_research_label_leakage():
    body = (
        "Zentrale Erkenntnisse auf einen Blick:** Barrierefreiheit ist wichtig. "
        "Viele wissen das nicht.\n\n"
        "Speicher dir das.\n\n"
        "#Barrierefrei #ÖPNV"
    )
    with pytest.raises(ValidationError, match="research-note leakage"):
        captions.validate_caption_variant("curiosity", body, "Ein anderes Skript.")


def test_validate_caption_variant_rejects_high_script_overlap():
    script = "Viele Kommunen nutzten Ausnahmen um Barrierefreiheit zu umgehen."
    body = (
        "Viele Kommunen nutzten Ausnahmen um Barrierefreiheit zu umgehen. "
        "Das betrifft fast alle Haltestellen.\n\n"
        "Speicher dir das.\n\n"
        "#Barrierefrei #ÖPNV"
    )
    with pytest.raises(ValidationError, match="repeats script"):
        captions.validate_caption_variant("curiosity", body, script)


def test_validate_caption_variant_rejects_missing_hashtags():
    body = (
        "Das sagt dir keiner: Viele Kommunen nutzten Ausnahmen. "
        "Ab 2026 ist Schluss damit.\n\n"
        "Speicher dir das."
    )
    with pytest.raises(ValidationError, match="hashtag"):
        captions.validate_caption_variant("curiosity", body, "Ein anderes Skript.")


# --- Bundle validation ---


def test_validate_caption_bundle_accepts_three_variants():
    parsed = {
        "variants": [
            {"key": "curiosity", "body": CURIOSITY_BODY},
            {"key": "personal", "body": PERSONAL_BODY},
            {"key": "provocative", "body": PROVOCATIVE_BODY},
        ]
    }
    result = captions.validate_caption_bundle(
        parsed, "Ein unabhängiges Skript das bewusst anders formuliert ist."
    )
    assert len(result["variants"]) == 3
    assert {v["key"] for v in result["variants"]} == set(VARIANT_KEYS)


def test_validate_caption_bundle_accepts_partial():
    parsed = {
        "variants": [
            {"key": "curiosity", "body": CURIOSITY_BODY},
            {"key": "provocative", "body": PROVOCATIVE_BODY},
        ]
    }
    result = captions.validate_caption_bundle(
        parsed, "Ein unabhängiges Skript."
    )
    assert len(result["variants"]) == 2


def test_validate_caption_bundle_raises_when_no_valid_variants():
    parsed = {"variants": [{"key": "curiosity", "body": "Zu kurz."}]}
    with pytest.raises(ValidationError):
        captions.validate_caption_bundle(parsed, "Ein Skript.")


# --- Selection ---


def test_select_caption_variant_key_is_deterministic():
    first = captions.select_caption_variant_key(
        topic_title="Thema", post_type="value", script="Script"
    )
    second = captions.select_caption_variant_key(
        topic_title="Thema", post_type="value", script="Script"
    )
    assert first == second
    assert first in VARIANT_KEYS


# --- Script hook extraction ---


def test_extract_script_hook_gets_first_sentence():
    script = (
        "Das sagt dir dein Verkehrsbetrieb nicht: Viele Kommunen nutzten Ausnahmen. "
        "Noch mehr Details hier."
    )
    hook = captions.extract_script_hook(script)
    assert hook == "Das sagt dir dein Verkehrsbetrieb nicht: Viele Kommunen nutzten Ausnahmen."


def test_extract_script_hook_returns_full_short_script():
    script = "Kurzer Satz ohne zweiten."
    hook = captions.extract_script_hook(script)
    assert hook == script


# --- Prompt building ---


def test_build_caption_prompt_includes_new_fields():
    prompt = captions._build_caption_prompt(
        topic_title="Barrierefreiheit",
        post_type="value",
        script="Ein Skript.",
        script_hook="Der Hook.",
        research_facts=["Fakt eins.", "Fakt zwei."],
    )
    assert "Der Hook." in prompt
    assert "1. Fakt eins." in prompt
    assert "2. Fakt zwei." in prompt
    assert "[curiosity]" in prompt
    assert "[personal]" in prompt
    assert "[provocative]" in prompt
    assert "[short_paragraph]" not in prompt


# --- Parse ---


def test_parse_text_variants_with_new_markers():
    text = (
        f"[curiosity]\n{CURIOSITY_BODY}\n\n"
        f"[personal]\n{PERSONAL_BODY}\n\n"
        f"[provocative]\n{PROVOCATIVE_BODY}"
    )
    parsed = captions._parse_text_variants(text)
    assert len(parsed["variants"]) == 3
    assert {v["key"] for v in parsed["variants"]} == set(VARIANT_KEYS)


# --- End-to-end generation ---


def test_generate_caption_bundle_with_new_structure():
    class FakeLLM:
        def generate_gemini_text(self, **kwargs):
            return (
                f"[curiosity]\n{CURIOSITY_BODY}\n\n"
                f"[personal]\n{PERSONAL_BODY}\n\n"
                f"[provocative]\n{PROVOCATIVE_BODY}"
            )

    bundle = captions.generate_caption_bundle(
        topic_title="Barrierefreiheit im ÖPNV",
        post_type="value",
        script="Ein komplett anderes Skript das bewusst nichts wiederholt.",
        context="Kontext",
        research_facts=["Viele Kommunen nutzten Ausnahmen.", "Ab 2026 gelten neue Regeln."],
        llm_factory=lambda: FakeLLM(),
    )
    assert len(bundle["variants"]) == 3
    assert bundle["selected_key"] in VARIANT_KEYS
    assert bundle["selected_body"]
    assert bundle["selection_reason"] == "hash_variant"


def test_generate_caption_bundle_raises_on_persistent_failure():
    class BadLLM:
        def generate_gemini_text(self, **kwargs):
            return "no markers here"

    with pytest.raises(ValidationError, match="failed after 3 attempts"):
        captions.generate_caption_bundle(
            topic_title="Barrierefreiheit im ÖPNV",
            post_type="value",
            script="Ein Skript zum Testen.",
            context="Kontext",
            research_facts=[],
            llm_factory=lambda: BadLLM(),
        )


def test_generate_caption_bundle_raises_on_llm_error():
    class ErrorLLM:
        def generate_gemini_text(self, **kwargs):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        captions.generate_caption_bundle(
            topic_title="Topic",
            post_type="value",
            script="Skript.",
            context="Kontext.",
            research_facts=[],
            llm_factory=lambda: ErrorLLM(),
        )


# --- attach_caption_bundle ---


def test_attach_caption_bundle_sets_description_and_caption():
    payload = {
        "script": "Ein komplett anderes Skript das bewusst nichts wiederholt.",
        "strict_seed": {
            "facts": [
                "Viele Kommunen nutzten Ausnahmen.",
                "Ab 2026 gelten neue Regeln.",
            ],
        },
    }
    enriched = captions.attach_caption_bundle(
        payload,
        topic_title="Barrierefreier ÖPNV",
        post_type="value",
        llm_factory=lambda: _make_stub_llm(),
    )
    assert enriched["caption_bundle"]["selected_key"] in VARIANT_KEYS
    assert enriched["description"] == enriched["caption_bundle"]["selected_body"]
    assert enriched["caption"] == enriched["caption_bundle"]["selected_body"]


def test_attach_caption_bundle_overwrites_preexisting_caption():
    payload = {
        "script": "Ein komplett anderes Skript das bewusst nichts wiederholt.",
        "caption": "Stale caption that should be overwritten.",
        "strict_seed": {"facts": ["Fakt eins."]},
    }
    enriched = captions.attach_caption_bundle(
        payload,
        topic_title="Barrierefreier ÖPNV",
        post_type="value",
        llm_factory=lambda: _make_stub_llm(),
    )
    assert enriched["caption"] != "Stale caption that should be overwritten."
    assert enriched["caption"] == enriched["caption_bundle"]["selected_body"]


# --- resolve_selected_caption ---


def test_resolve_selected_caption_prefers_bundle():
    seed_data = {
        "caption": "Stale.",
        "description": "Legacy.",
        "caption_bundle": {
            "selected_body": CURIOSITY_BODY,
            "selection_reason": "hash_variant",
        },
    }
    assert captions.resolve_selected_caption(seed_data) == CURIOSITY_BODY


def test_resolve_selected_caption_falls_back_to_caption():
    seed_data = {"caption": "Fallback caption.", "description": "Legacy."}
    assert captions.resolve_selected_caption(seed_data) == "Fallback caption."


# --- default_publish_caption ---


def test_default_publish_caption_prefers_caption_bundle():
    post = {
        "publish_caption": "",
        "seed_data": {
            "description": "Legacy description",
            "caption_bundle": {"selected_body": CURIOSITY_BODY},
        },
    }
    assert publish_handlers._default_publish_caption(post) == CURIOSITY_BODY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_caption_generation.py -v`
Expected: Multiple failures because `captions.py` still has old structure (old keys, no `extract_script_hook`, old `_build_caption_prompt` signature, etc.)

- [ ] **Step 3: Rewrite captions.py — remove old machinery, add new structure**

Open `app/features/topics/captions.py` and make these changes:

**A. Replace constants at top of file (lines 22-134).** Remove `FAMILY_ORDER`, `FAMILY_SPECS`, all `_FALLBACK_*` dicts (openers, support sentences, bullets, default hashtags). Keep `_COMMON_ENGLISH_WORDS` (used by `_looks_mixed_language`) and `_TITLE_STOPWORDS` (used by `_meaningful_title_tokens`). Replace the removed constants with:

```python
VARIANT_KEYS = ("curiosity", "personal", "provocative")
CAPTION_MIN_CHARS = 150
CAPTION_MAX_CHARS = 300
CAPTION_HOOK_MAX_CHARS = 100
```

**B. Keep these utility functions unchanged:**
- `_normalize_line_breaks()` (line 137)
- `_split_paragraphs()` (line 145)
- `_extract_hashtags()` (line 161)
- `_count_emojis()` (line 165)
- `_dedupe_preserve_order()` (line 193)
- `_script_overlap_ratio()` (line 490)
- `_looks_mixed_language()` (line 499)
- `_meaningful_title_tokens()` (line 507)
- `_caption_looks_like_title()` (line 512)
- `_resolve_canonical_topic()` (line 530)

**C. Delete these functions entirely:**
- `_split_sentences()` (line 169)
- `_choice_from_pool()` (line 205)
- `_normalize_hashtag_token()` (line 210)
- `_build_hashtags()` (line 215)
- `_compress_sentence()` (line 228)
- `_ensure_minimum_sentence()` (line 261)
- `_material_sentences()` (line 271)
- `_build_caption_material()` (line 285)
- `_pick_fact_sentence()` (line 324)
- `_build_short_paragraph_variant()` (line 338)
- `_build_medium_bullets_variant()` (line 354)
- `_build_long_structured_variant()` (line 390)
- `_repair_caption_bundle()` (line 431)
- `_paragraph_contains_structured_list()` (line 474)
- `_index_of_first_structured_paragraph()` (line 482)
- `_bullet_lines()` (line 151)
- `_numbered_lines()` (line 156)
- `_caption_variant_pool()` (line 664)

**D. Add `extract_script_hook()` function:**

```python
def extract_script_hook(script: str) -> str:
    """Extract the first sentence of the script as the spoken hook."""
    text = _normalize_line_breaks(script).replace("\n", " ").strip()
    if not text:
        return ""
    match = re.search(r"[.!?]", text)
    if match:
        return text[: match.end()].strip()
    return text
```

**E. Rewrite `_build_caption_prompt()` to accept new parameters:**

```python
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
```

**F. Rewrite `_parse_text_variants()` to use new markers:**

```python
_MARKER_PATTERN = re.compile(
    r"^\[(curiosity|personal|provocative)\]\s*$", re.IGNORECASE
)
```

Keep the rest of `_parse_text_variants()` logic unchanged — it already parses by marker.

**G. Rewrite `validate_caption_variant()`:**

```python
def validate_caption_variant(key: str, body: str, script: str) -> Dict[str, Any]:
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
    if _script_overlap_ratio(script, normalized) > 0.55:
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
```

**H. Rewrite `validate_caption_bundle()`:**

```python
def validate_caption_bundle(bundle: Dict[str, Any], script: str, post_type: str = "") -> Dict[str, Any]:
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
            validated.append(validate_caption_variant(key, body, script))
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
```

**I. Rewrite `select_caption_variant_key()`:**

```python
def select_caption_variant_key(*, topic_title: str, post_type: str, script: str) -> str:
    digest = hashlib.sha256(f"{topic_title}|{post_type}|{script}".encode("utf-8")).hexdigest()
    return VARIANT_KEYS[int(digest[:8], 16) % len(VARIANT_KEYS)]
```

**J. Rewrite `_select_best_variant()`:**

```python
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
```

**K. Rewrite `generate_caption_bundle()` — add `research_facts` parameter, remove repair path:**

```python
def generate_caption_bundle(
    *,
    topic_title: str,
    post_type: str,
    script: str,
    context: str,
    llm_factory: Optional[Callable] = None,
    canonical_topic: Optional[str] = None,
    research_facts: Optional[List[str]] = None,
    fallback_facts: Optional[List[str]] = None,
    allow_repair: bool = False,
) -> Dict[str, Any]:
    canonical_topic = str(canonical_topic or topic_title or "").strip()
    selected_key = select_caption_variant_key(topic_title=canonical_topic, post_type=post_type, script=script)
    script_hook = extract_script_hook(script)
    facts = list(research_facts or fallback_facts or [])
    llm_factory = llm_factory or get_llm_client
    llm = llm_factory()
    prompt = _build_caption_prompt(
        topic_title=canonical_topic,
        post_type=post_type,
        script=script,
        script_hook=script_hook,
        research_facts=facts,
    )
    last_error: Optional[ValidationError] = None
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
            bundle = validate_caption_bundle(parsed, script, post_type=post_type)
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
```

**L. Rewrite `attach_caption_bundle()` — extract research facts from seed payload:**

```python
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
    research_facts = sanitize_fact_fragments(list(strict_seed.get("facts") or []))
    context_candidates = [
        context,
        payload.get("source_summary"),
        payload.get("description"),
        payload.get("research_caption"),
        strict_seed.get("source_context"),
    ]
    derived_context_parts = _dedupe_preserve_order(
        [sanitize_metadata_text(value, max_sentences=3) for value in context_candidates if sanitize_metadata_text(value, max_sentences=3)]
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
        research_facts=research_facts,
    )
    payload["canonical_topic"] = canonical_topic
    payload["caption_bundle"] = bundle
    payload["description"] = bundle["selected_body"]
    payload["caption"] = bundle["selected_body"]
    return payload
```

**M. Remove unused imports.** After deleting all the fallback/family functions, remove any imports that are no longer needed (e.g., `_BULLET_PATTERN`, `_NUMBERED_PATTERN`, `_SENTENCE_PATTERN`, `_CAPTION_ABBREVIATIONS` if only used by deleted functions). Keep `sanitize_fact_fragments` and `sanitize_metadata_text`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_caption_generation.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: No regressions. If other test files import old constants like `FAMILY_ORDER` or `FAMILY_SPECS`, they will need updating in Task 4.

- [ ] **Step 6: Commit**

```bash
git add app/features/topics/captions.py tests/test_caption_generation.py
git commit -m "feat(captions): rewrite to Hook-Body-CTA with 3 emotional-angle variations

Remove 300+ lines of fallback machinery (FALLBACK_OPENERS, FALLBACK_BULLETS,
family-specific builders, repair path). Replace with research-driven generation
using curiosity/personal/provocative hooks. New validation checks length, CTA,
script overlap, and topical alignment."
```

---

### Task 3: Update handlers.py to Pass Research Facts

**Files:**
- Modify: `app/features/topics/handlers.py:73-89`

- [ ] **Step 1: Read the current `_attach_publish_captions` function**

Read `app/features/topics/handlers.py` lines 73-89 to confirm the current signature.

- [ ] **Step 2: No changes needed to `_attach_publish_captions`**

The function delegates to `attach_caption_bundle()` which already extracts `research_facts` from `strict_seed.facts` internally (see Task 2 Step 3L). The handler signature stays unchanged. The research facts flow through the existing `seed_payload` parameter.

Verify by reading all call sites (lines ~556, ~707, ~857, ~923) — they all pass `seed_payload` which contains `strict_seed.facts`. No handler changes needed.

- [ ] **Step 3: Verify by running tests**

Run: `pytest tests/ -v --tb=short -k "caption"`
Expected: All caption-related tests pass.

---

### Task 4: Fix Any Regressions in Other Test Files

**Files:**
- Possibly modify: `tests/test_caption_worker.py`, `tests/test_caption_aligner.py`, `tests/test_caption_renderer.py`, `tests/test_caption_status_constants.py`

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: Identify any failures caused by removed constants (`FAMILY_ORDER`, `FAMILY_SPECS`) or changed function signatures.

- [ ] **Step 2: Fix any failing tests**

For each failure:
- If the test imports `FAMILY_ORDER` or `FAMILY_SPECS`: replace with `VARIANT_KEYS`
- If the test uses old variant keys (`short_paragraph`, `medium_bullets`, `long_structured`): replace with new keys (`curiosity`, `personal`, `provocative`)
- If the test calls `generate_caption_bundle` without `research_facts`: add `research_facts=[]`
- If the test references deleted functions: remove or rewrite the test

- [ ] **Step 3: Run the full test suite again**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: update caption tests for new Hook-Body-CTA structure"
```

---

### Task 5: Verify End-to-End with Linting

- [ ] **Step 1: Run ruff**

Run: `ruff check app/features/topics/captions.py`
Expected: No lint errors. Fix any issues (unused imports from deleted functions, etc.)

- [ ] **Step 2: Run full linter**

Run: `ruff check .`
Expected: No new lint errors introduced by this change.

- [ ] **Step 3: Run full test suite one final time**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 4: Commit any lint fixes**

```bash
git add -A && git commit -m "chore: lint fixes for caption redesign"
```
