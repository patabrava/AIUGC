# Text-First Caption Generation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch caption generation from JSON-first (which Gemini breaks with unterminated strings) to text-first with marker parsing, and generate captions only at post-creation time instead of during harvesting.

**Architecture:** Replace `generate_gemini_json` with `generate_gemini_text` using `[short_paragraph]`/`[medium_bullets]`/`[long_structured]` markers that `_parse_text_variants` already parses. Remove premature caption generation from hub.py harvesting path. Fix `resolve_selected_caption` priority and `attach_caption_bundle` caption overwrite bug.

**Tech Stack:** Python 3.11, FastAPI, Gemini API (text generation), pytest

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `app/features/topics/captions.py` | Modify | Switch `generate_caption_bundle` to text-first, fix `resolve_selected_caption` priority, fix `attach_caption_bundle` caption overwrite |
| `app/features/topics/prompt_data/captions_prompt.txt` | Modify | Change output format instructions from JSON to marker format |
| `app/features/topics/hub.py` | Modify | Remove `attach_caption_bundle` call from harvesting path |
| `tests/test_caption_generation.py` | Modify | Update tests for text-first flow, add marker parsing tests, fix priority tests |

---

### Task 1: Fix `resolve_selected_caption` Priority

The current priority checks `caption` before `caption_bundle.selected_body`, which means stale research captions override the generated bundle.

**Files:**
- Modify: `tests/test_caption_generation.py`
- Modify: `app/features/topics/captions.py:361-372`

- [ ] **Step 1: Write failing test — bundle body takes priority over caption field**

```python
def test_resolve_selected_caption_prefers_bundle_over_stale_caption():
    """When caption_bundle.selected_body exists, it wins over seed_data.caption."""
    seed_data = {
        "caption": "Stale research caption without hashtags.",
        "description": "Legacy description",
        "caption_bundle": {
            "selected_body": MEDIUM_BODY,
            "selection_reason": "hash_variant",
        },
    }
    assert captions.resolve_selected_caption(seed_data) == MEDIUM_BODY
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_caption_generation.py::test_resolve_selected_caption_prefers_bundle_over_stale_caption -v`

Expected: FAIL — currently returns "Stale research caption without hashtags." because `caption` is checked first.

- [ ] **Step 3: Fix `resolve_selected_caption` — check bundle first**

In `app/features/topics/captions.py`, replace lines 361-372:

```python
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
```

- [ ] **Step 4: Update the old priority test to match new behavior**

The test `test_resolve_selected_caption_prefers_existing_caption_over_fallback_bundle` (line 210) asserts the OLD wrong behavior. Update it:

```python
def test_resolve_selected_caption_prefers_bundle_over_caption_even_for_fallback():
    """caption_bundle.selected_body always wins, even if it's from fallback."""
    seed_data = {
        "caption": "Das ist die eigentlich gewollte Caption mit sauberem Deutsch.",
        "description": "Legacysummary",
        "caption_bundle": {
            "selected_body": "Titel wurde irrtuemlich in die Caption kopiert.",
            "selection_reason": "fallback_hash_variant",
        },
    }
    # Bundle body wins over caption field
    assert captions.resolve_selected_caption(seed_data) == seed_data["caption_bundle"]["selected_body"]
```

- [ ] **Step 5: Run all caption tests**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_caption_generation.py -v`

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC
git add app/features/topics/captions.py tests/test_caption_generation.py
git commit -m "fix: resolve_selected_caption prefers caption_bundle over stale caption field"
```

---

### Task 2: Fix `attach_caption_bundle` Caption Overwrite

The `or` pattern on line 495 keeps stale caption values instead of overwriting with the bundle body.

**Files:**
- Modify: `tests/test_caption_generation.py`
- Modify: `app/features/topics/captions.py:495`

- [ ] **Step 1: Write failing test — bundle overwrites pre-existing caption**

```python
def test_attach_caption_bundle_overwrites_preexisting_caption():
    """Even when caption has a value, attach_caption_bundle must overwrite it with bundle body."""
    llm = _StubLLM(
        {
            "variants": [
                {"key": "short_paragraph", "body": SHORT_BODY},
                {"key": "medium_bullets", "body": MEDIUM_BODY},
                {"key": "long_structured", "body": LONG_BODY},
            ]
        }
    )
    payload = {
        "script": "Kurzes Skript, aber nicht identisch mit der Caption.",
        "caption": "Stale research caption that should be overwritten.",
        "strict_seed": {"facts": ["Fakt eins"]},
    }
    enriched = captions.attach_caption_bundle(
        payload,
        topic_title="Barrierefreier ÖPNV",
        post_type="value",
        llm_factory=lambda: llm,
    )
    assert enriched["caption"] == enriched["caption_bundle"]["selected_body"]
    assert enriched["caption"] != "Stale research caption that should be overwritten."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_caption_generation.py::test_attach_caption_bundle_overwrites_preexisting_caption -v`

Expected: FAIL — currently keeps "Stale research caption" because of the `or` pattern.

- [ ] **Step 3: Fix `attach_caption_bundle` — unconditionally set caption**

In `app/features/topics/captions.py`, change line 495 from:

```python
    payload["caption"] = payload.get("caption") or bundle["selected_body"]
```

to:

```python
    payload["caption"] = bundle["selected_body"]
```

- [ ] **Step 4: Run all caption tests**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_caption_generation.py -v`

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC
git add app/features/topics/captions.py tests/test_caption_generation.py
git commit -m "fix: attach_caption_bundle always overwrites caption with bundle body"
```

---

### Task 3: Switch Prompt to Text-First Marker Format

Replace the JSON output instruction in the prompt with marker-based format. Keep all the quality/style rules unchanged.

**Files:**
- Modify: `app/features/topics/prompt_data/captions_prompt.txt`
- Modify: `tests/test_caption_generation.py`

- [ ] **Step 1: Write test that verifies prompt no longer asks for JSON**

```python
def test_build_caption_prompt_requests_marker_format_not_json():
    prompt = captions._build_caption_prompt(
        topic_title="Barrierefreiheit",
        post_type="value",
        script="Ein Skript.",
        context="Kontext",
    )
    assert "[short_paragraph]" in prompt
    assert "[medium_bullets]" in prompt
    assert "[long_structured]" in prompt
    assert "JSON" not in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_caption_generation.py::test_build_caption_prompt_requests_marker_format_not_json -v`

Expected: FAIL — current prompt says "Gib ausschliesslich valides JSON zurueck".

- [ ] **Step 3: Update the prompt file**

Replace `app/features/topics/prompt_data/captions_prompt.txt` with:

```
CAPTIONS_PROMPT
Erstelle exakt 3 deutsche Caption-Varianten fuer einen UGC-Post fuer Rollstuhlnutzer:innen in Deutschland.

Thema: {topic_title}
Post-Typ: {post_type}
Gesprochenes Skript: {script}
Kontext: {context}

Gib die drei Varianten im folgenden Markerformat zurueck. Schreibe jede Caption direkt unter ihren Marker. Kein JSON, keine Code-Bloecke, keine zusaetzliche Formatierung.

[short_paragraph]
(Caption hier)

[medium_bullets]
(Caption hier)

[long_structured]
(Caption hier)

Das Feld `Thema` ist nur Metadaten-Kontext: wiederhole den exakten Titel nicht im Einstieg und kopiere ihn nicht wörtlich in die Caption.
Starte nicht mit dem Topic-Titel oder einer fast identischen Umformulierung davon.
Schreibe die Caption immer als eigenstaendige Social-Caption, nicht als angehaengte Titelzeile.
Nutze den Titel nur als Hintergrundkontext, nicht als Textbaustein.
Wenn dir ein Einstieg wie ein Titel klingt, schreibe ihn neu als Konsequenz, Frage, Nutzen oder Warnung.
Verwende statt Titelworten lieber die echte Auswirkung fuer die Zielgruppe.
Keine Caption darf mit "Bei" plus Titel oder einer sehr nahen Variante davon beginnen.
Jede der drei Varianten braucht einen klar anderen Einstieg. Verwende unterschiedliche Hook-Formen, damit short_paragraph, medium_bullets und long_structured nicht mit fast demselben Satz beginnen.
Die drei Hooks muessen deutlich verschieden klingen: Frage, Konsequenz, Warnung, Nutzen, Beobachtung oder kurzer Alltagskonflikt sind erlaubt, aber nicht dieselbe Schablone dreimal.
Verwende nicht fuer alle drei Varianten dieselbe Satzschablone wie "Wenn es um ..." oder "Rund um ...".
Vermeide generische Platzhalter wie "diesem Thema", "diesem Fall", "hier" oder "Kontext"; nenne lieber den konkreten Alltagseffekt oder die konkrete Situation.

Strukturregeln:
- short_paragraph: 140-260 Zeichen, genau 1 Absatz, keine Stichpunkte, keine Nummerierung.
- medium_bullets: 220-420 Zeichen, Hook-Absatz, dann Leerzeile, dann 2-3 Stichpunkte mit `• `. Wenn die Caption laenger wird, fuege vor den Stichpunkten einen zweiten kurzen Absatz ein.
- long_structured: 350-700 Zeichen, immer 2 kurze Prosa-Absaetze vor der Liste, dann Leerzeile, dann 3-4 Stichpunkte mit `• ` oder 2-4 nummerierte Zeilen, nur wenn eine Reihenfolge sinnvoll ist.
- Nutze echte Absätze mit echten Zeilenumbrüchen.
- Kein Textblock ohne Struktur bei medium_bullets oder long_structured.
- Lange Captions duerfen nie aus einem einzigen grossen Absatz vor der Liste bestehen.
- Wiederhole das Skript nicht einfach.
- Schreibe direkt, glaubwürdig, hilfreich, teilbar.
- Orientiere dich an konkreten Folgen im Alltag statt am Titel.
- Nutze maximal 1 Emoji pro Caption, sparsam im Hook oder in einem einzelnen Punkt.
- Ergaenze mehr praktische Einordnung oder konkrete Konsequenzen statt nur eine knappe Zusammenfassung.
- Ergänze Kontext, Einordnung oder praktische Hinweise ohne neue Fakten zu erfinden.
- Jede Variante endet mit 2-4 thematisch passenden Hashtags.
- Alles vollständig auf Deutsch.
```

- [ ] **Step 4: Update the existing prompt content test**

The test `test_build_caption_prompt_discourages_title_copying` (line 278) checks for specific phrases. Update line 286 assertion since "Gib ausschliesslich valides JSON" is gone:

```python
def test_build_caption_prompt_discourages_title_copying():
    prompt = captions._build_caption_prompt(
        topic_title="Gesetzliche Rahmenbedingungen Fristen Ausnahmeregelungen",
        post_type="value",
        script="Das Skript ist bewusst kurz und endet mit einem Punkt.",
        context="Kontext",
    )

    assert "Das Feld `Thema` ist nur Metadaten-Kontext" in prompt
    assert "wiederhole den exakten Titel nicht" in prompt
    assert "Starte nicht mit dem Topic-Titel" in prompt
    assert "Nutze maximal 1 Emoji pro Caption" in prompt
    assert "Bei" in prompt and "plus Titel" in prompt
    assert "Jede der drei Varianten braucht einen klar anderen Einstieg" in prompt
    assert "Vermeide generische Platzhalter" in prompt
    assert "[short_paragraph]" in prompt
    assert "[medium_bullets]" in prompt
    assert "[long_structured]" in prompt
```

- [ ] **Step 5: Run all caption tests**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_caption_generation.py -v`

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC
git add app/features/topics/prompt_data/captions_prompt.txt tests/test_caption_generation.py
git commit -m "feat: switch caption prompt from JSON to text marker format"
```

---

### Task 4: Switch `generate_caption_bundle` to Text-First

Replace the JSON-first approach with text-first using `generate_gemini_text` and `_parse_text_variants`. Remove the JSON schema and `generate_gemini_json` call entirely.

**Files:**
- Modify: `app/features/topics/captions.py:376-452`
- Modify: `tests/test_caption_generation.py`

- [ ] **Step 1: Write test for text-first generation with marker format**

```python
def test_generate_caption_bundle_text_first_with_markers():
    """generate_caption_bundle should call generate_gemini_text, not generate_gemini_json."""
    marker_response = (
        "[short_paragraph]\n"
        f"{SHORT_BODY}\n\n"
        "[medium_bullets]\n"
        f"{MEDIUM_BODY}\n\n"
        "[long_structured]\n"
        f"{LONG_BODY}"
    )

    class TextLLM:
        def generate_gemini_text(self, **kwargs):
            return marker_response

    bundle = captions.generate_caption_bundle(
        topic_title="Barrierefreier ÖPNV",
        post_type="value",
        script="Das Skript ist bewusst anders formuliert und endet mit einem Punkt.",
        context="Kontext mit ausreichend Text fuer die Varianten.",
        llm_factory=lambda: TextLLM(),
    )

    assert len(bundle["variants"]) == 3
    assert {v["key"] for v in bundle["variants"]} == set(captions.FAMILY_ORDER)
    assert bundle["selected_body"]
    assert bundle["selection_reason"] == "hash_variant"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_caption_generation.py::test_generate_caption_bundle_text_first_with_markers -v`

Expected: FAIL — current code calls `generate_gemini_json` which doesn't exist on `TextLLM`.

- [ ] **Step 3: Rewrite `generate_caption_bundle` to use text-first**

In `app/features/topics/captions.py`, replace the `generate_caption_bundle` function (lines 376-452):

```python
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
```

- [ ] **Step 4: Update `_StubLLM` in tests to return text instead of JSON**

Replace `_StubLLM` class at test line 38:

```python
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
```

- [ ] **Step 5: Update the `FakeLLM` in `test_generate_caption_bundle_falls_back_to_synthesized_bundle` (line 236)**

The existing `FakeLLM` raises `RuntimeError` on `generate_gemini_json`. Update it to raise on `generate_gemini_text` instead:

```python
def test_generate_caption_bundle_falls_back_to_synthesized_bundle(monkeypatch):
    class FakeLLM:
        def generate_gemini_text(self, *args, **kwargs):
            raise RuntimeError("boom")

    bundle = captions.generate_caption_bundle(
        topic_title="Topic A",
        post_type="value",
        script=(
            "Erster langer Skripttext mit genug Inhalt, damit auch die längeren Caption-Familien "
            "ihre Mindestlaenge erreichen koennen."
        ),
        context="Kontext A mit ausreichend Text fuer die laengeren Varianten und saubere Validierung.",
        llm_factory=lambda: FakeLLM(),
    )

    assert bundle["selected_key"] in {"medium_bullets", "long_structured"}
    assert len(bundle["variants"]) == 3
    assert bundle["selected_body"]
    assert bundle["selection_reason"] in {"fallback_hash_variant", "hash_variant"}
```

- [ ] **Step 6: Update `test_generate_caption_bundle_fallback_handles_long_topic_and_context` (line 261)**

```python
def test_generate_caption_bundle_fallback_handles_long_topic_and_context():
    class FakeLLM:
        def generate_gemini_text(self, *args, **kwargs):
            raise RuntimeError("boom")

    bundle = captions.generate_caption_bundle(
        topic_title="Sehr langer Titel " * 20,
        post_type="value",
        script="Das ist ein unabhängiges Skript mit ausreichender Länge und klarer Satzstruktur.",
        context="Kontext " * 200,
        llm_factory=lambda: FakeLLM(),
    )

    assert len(bundle["variants"]) == 3
    assert {item["key"] for item in bundle["variants"]} == set(captions.FAMILY_ORDER)
```

- [ ] **Step 7: Update `test_generate_caption_bundle_uses_canonical_topic_for_title_checks` (line 296)**

Change `FakeLLM` to return text with markers instead of a dict:

```python
def test_generate_caption_bundle_uses_canonical_topic_for_title_checks(monkeypatch):
    captured = {}

    class FakeLLM:
        def generate_gemini_text(self, **kwargs):
            captured["prompt"] = kwargs["prompt"]
            return (
                f"[short_paragraph]\n{SHORT_BODY}\n\n"
                f"[medium_bullets]\n{MEDIUM_BODY}\n\n"
                f"[long_structured]\n{LONG_BODY}"
            )

    bundle = captions.generate_caption_bundle(
        topic_title="Rechtliche Grundlagen Zielsetzung - Forschungsdossier: Barrierefreiheit im ÖPNV-Alltag",
        canonical_topic="Barrierefreiheit im ÖPNV",
        post_type="value",
        script="Das Skript ist bewusst anders formuliert und endet mit einem Punkt.",
        context="Kontext",
        llm_factory=lambda: FakeLLM(),
    )

    assert bundle["selected_key"] == "medium_bullets"
    assert "Barrierefreiheit im ÖPNV" in captured["prompt"]
    assert "Forschungsdossier" not in captured["prompt"]
```

- [ ] **Step 8: Update `test_generate_caption_bundle_rejects_title_like_opening` (line 352)**

Change to return text markers instead of a dict:

```python
def test_generate_caption_bundle_rejects_title_like_opening(monkeypatch):
    class FakeLLM:
        def generate_gemini_text(self, *args, **kwargs):
            return (
                "[short_paragraph]\n"
                "Bei Gesetzliche Rahmenbedingungen Fristen hilft dir ein klarer Blick. #Tag1 #Tag2\n\n"
                "[medium_bullets]\n"
                "Bei Gesetzliche Rahmenbedingungen Fristen hilft dir ein klarer Blick.\n\n"
                "• Punkt eins.\n"
                "• Punkt zwei.\n\n"
                "#Tag1 #Tag2 #Tag3\n\n"
                "[long_structured]\n"
                "Bei Gesetzliche Rahmenbedingungen Fristen hilft dir ein klarer Blick.\n\n"
                "Noch mehr Einordnung.\n\n"
                "1. Punkt eins.\n"
                "2. Punkt zwei.\n\n"
                "#Tag1 #Tag2 #Tag3"
            )

    bundle = captions.generate_caption_bundle(
        topic_title="Gesetzliche Rahmenbedingungen Fristen Ausnahmeregelungen",
        post_type="value",
        script="Das Skript ist bewusst kurz und endet mit einem Punkt.",
        context="Kontext",
        llm_factory=lambda: FakeLLM(),
    )

    assert bundle["selection_reason"] == "fallback_hash_variant"
```

- [ ] **Step 9: Remove unused import `from pathlib import Path`**

Check if `Path` is still used elsewhere in `captions.py`. It's used in `_load_caption_prompt_template` (line 239), so keep it.

- [ ] **Step 10: Run all caption tests**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_caption_generation.py -v`

Expected: All PASS.

- [ ] **Step 11: Commit**

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC
git add app/features/topics/captions.py tests/test_caption_generation.py
git commit -m "feat: switch generate_caption_bundle from JSON to text-first with marker parsing"
```

---

### Task 5: Remove Caption Generation from Hub Harvesting

Caption generation during topic bank harvesting is premature and wastes Gemini calls. Remove it — captions will only be generated at post creation time.

**Files:**
- Modify: `app/features/topics/hub.py:822-829`

- [ ] **Step 1: Remove `attach_caption_bundle` call from hub.py**

In `app/features/topics/hub.py`, remove lines 822-829:

```python
        seed_payload = attach_caption_bundle(
            seed_payload,
            topic_title=lane_title,
            post_type=post_type,
            script_fallback=topic_data.rotation,
            context=str(seed_payload.get("description") or seed_payload.get("research_caption") or lane_title),
            canonical_topic=str(research_dossier.get("seed_topic") or research_dossier.get("topic") or lane_title).strip(),
        )
```

So that the flow goes directly from `build_seed_payload` (line 812-821) to `_persist_topic_bank_row` (line 830).

- [ ] **Step 2: Remove the `attach_caption_bundle` import from hub.py if no longer used**

Check if `attach_caption_bundle` is used elsewhere in hub.py:

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && grep -n "attach_caption_bundle" app/features/topics/hub.py`

If line 822-829 was the only usage, remove the import at the top of hub.py.

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/ -v --timeout=30 2>&1 | tail -30`

Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC
git add app/features/topics/hub.py
git commit -m "refactor: remove premature caption generation from topic bank harvesting"
```

---

### Task 6: Ensure Post Creation Path Generates Fresh Captions

Verify that the stored suggestion path (`handlers.py:553`) and the direct research path (`handlers.py:994`) correctly generate captions at post creation time, even when the seed_payload has no pre-existing `caption_bundle`.

**Files:**
- Modify: `app/features/topics/handlers.py:553-560` (stored suggestion path)

- [ ] **Step 1: Verify stored suggestion path handles missing caption_bundle**

Read the stored suggestion flow at `handlers.py:553`. The current code calls `_attach_publish_captions` which calls `attach_caption_bundle`. With Task 5 removing the bundle from harvesting, `seed_payload` from stored suggestions will no longer have `caption_bundle`. This means `attach_caption_bundle` will skip the early return (line 467) and generate a fresh bundle. This is correct behavior — no code change needed.

- [ ] **Step 2: Verify the early-return path still works for re-runs**

If a post is re-created with a seed_payload that already has a caption_bundle (from a previous post creation), the early return at `attach_caption_bundle:467` should still work correctly with the fixed `resolve_selected_caption`. Already verified by Task 1.

- [ ] **Step 3: Run full test suite to confirm nothing breaks**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/ -v --timeout=30 2>&1 | tail -30`

Expected: All PASS.

- [ ] **Step 4: Commit (only if changes were needed)**

No commit expected — this task is verification only.

---

### Task 7: Add Integration Test for Marker Parsing

Add a test that verifies the full round-trip: marker text → `_parse_text_variants` → `validate_caption_bundle`.

**Files:**
- Modify: `tests/test_caption_generation.py`

- [ ] **Step 1: Write integration test for marker parsing round-trip**

```python
def test_parse_text_variants_round_trip():
    """Marker-formatted text should parse and validate as a complete caption bundle."""
    marker_text = (
        "[short_paragraph]\n"
        f"{SHORT_BODY}\n\n"
        "[medium_bullets]\n"
        f"{MEDIUM_BODY}\n\n"
        "[long_structured]\n"
        f"{LONG_BODY}"
    )
    parsed = captions._parse_text_variants(marker_text)
    assert len(parsed["variants"]) == 3
    assert {v["key"] for v in parsed["variants"]} == set(captions.FAMILY_ORDER)

    validated = captions.validate_caption_bundle(
        parsed, "Ein unabhaengiges Skript das absichtlich anders formuliert ist."
    )
    assert len(validated["variants"]) == 3
    for variant in validated["variants"]:
        assert variant["char_count"] >= captions.FAMILY_SPECS[variant["key"]]["min_chars"]
```

- [ ] **Step 2: Write test for partial marker parsing (missing variant)**

```python
def test_parse_text_variants_missing_variant_raises():
    """If Gemini only returns 2 of 3 markers, validation should fail."""
    marker_text = (
        "[short_paragraph]\n"
        f"{SHORT_BODY}\n\n"
        "[medium_bullets]\n"
        f"{MEDIUM_BODY}"
    )
    parsed = captions._parse_text_variants(marker_text)
    assert len(parsed["variants"]) == 2

    with pytest.raises(ValidationError, match="three expected families"):
        captions.validate_caption_bundle(
            parsed, "Ein unabhaengiges Skript."
        )
```

- [ ] **Step 3: Run all caption tests**

Run: `cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/test_caption_generation.py -v`

Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC
git add tests/test_caption_generation.py
git commit -m "test: add marker parsing integration tests for text-first caption flow"
```
