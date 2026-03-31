# Caption Generation Refactor — Full Changelog

**Date:** 2026-03-30 / 2026-03-31
**Base SHA:** `b4e6d93`
**Final SHA:** `560fc7c`
**Net impact:** 389 insertions, 1056 deletions across 4 files

---

## Problem

The caption generation system was producing generic, disconnected captions that had nothing to do with the actual script or research data. The screenshot evidence showed a script about the 2026 Barrierefreiheit/ÖPNV deadline generating a caption like "Oft machen kleine Strukturen den Unterschied" — a generic self-help filler that could be about anything.

### Root Causes Identified

1. **Fallback-heavy architecture** — ~150 lines of hardcoded German fallback sentences (`_FALLBACK_OPENERS`, `_FALLBACK_SUPPORT_SENTENCES`, `_FALLBACK_BULLETS`). When LLM output failed validation, the repair path silently assembled captions entirely from these generic templates.

2. **Research data barely reached the caption prompt** — The `context` variable was built from sanitized, truncated fragments via `sanitize_metadata_text(max_sentences=3)`. By the time research passed through compression, the specific, interesting facts were lost.

3. **Weak prompt** — Told the LLM "here's a script and context, make 3 formats" with no guidance on hook psychology, CTA inclusion, or research fact usage.

4. **No TikTok engagement strategy** — No curiosity gaps, no CTAs, no complementary hooks. The 3-format system (short_paragraph / medium_bullets / long_structured) was arbitrary format gymnastics that didn't map to any real platform difference.

---

## Research Conducted

### TikTok/Instagram Caption Trends (2025-2026)

**Sources consulted:** OpusClip, TTS Vibes, Akselera, SendShort, Sprout Social, Buffer, Hootsuite, Socialinsider, TrueFuture Media, Napolify, Blitzcut, TikAdSuite, Sovran, Creatify, WebFX, Go-Viral, RecurPost, ALM Corp, TikTok Business, MarketingAgent Blog, and several German-language TikTok resources.

**Key findings:**

| Finding | Data |
|---------|------|
| CTAs boost visibility | +55.7% |
| Questions in captions drive more comments | +44% |
| Short captions (<100 chars visible) engagement | +21% higher |
| Complementary hooks (caption ≠ script) save rate | +10-25% higher |
| Verbatim repetition (caption = script) watch time | -5-15% |
| Saves/shares as algorithm signal | #1 in 2026 |

### Winning Structure

One structure consistently outperformed all others:

```
[HOOK — <100 chars, keyword in first 30 chars]
[BODY — 1-2 sentences, specific fact, <150 chars]
[CTA — direct action ask: save/share/comment]
[3-5 CamelCase hashtags]
```

Total: 150-300 chars (ideal), validation accepts 80-400 for LLM tolerance.

### Caption vs Script Hook Relationship

- Caption hook must COMPLEMENT the script hook, never repeat it
- Same topic, different emotional angle, additive keywords
- Different keywords across caption vs script expand algorithmic surface area (reach adjacent audiences)
- The caption is a "second chance" hook for muted viewers or slow scrollers

---

## Design Decisions

### 1. Single structure, 3 emotional-angle variations

Replaced 3 FORMAT variants (short_paragraph / medium_bullets / long_structured) with 3 EMOTIONAL ANGLE variants on the same Hook→Body→CTA structure:

- **curiosity** — Social proof / curiosity gap ("Das sagt dir dein Verkehrsbetrieb nicht:")
- **personal** — Relatable / affected-person angle ("Wenn du ÖPNV fährst, betrifft dich das ab 2026.")
- **provocative** — Challenge / pattern interrupt ("Keine Rampe, kein Aufzug — und trotzdem barrierefrei?")

### 2. Research facts passed directly to LLM

Instead of compressing research into a `context` string via `sanitize_metadata_text(max_sentences=3)`, research facts are now passed as a numbered list directly to the prompt. The LLM picks the most engaging ones.

### 3. Script hook extraction

New `extract_script_hook()` function extracts the first sentence of the script. This is passed to the LLM explicitly so it knows what NOT to repeat in the caption.

### 4. Adaptive overlap threshold

- With research facts: 0.55 overlap threshold (strict — LLM has alternative material)
- Without research facts: 0.85 overlap threshold (relaxed — LLM only has the script to draw from)

### 5. Pipeline timing unchanged

Captions still generate during batch seeding (S2_SEEDED), using the bank script. No pipeline timing changes.

---

## Files Changed

### `app/features/topics/captions.py` — Major rewrite

**Before:** 846 lines | **After:** 391 lines | **Delta:** -455 lines (-54%)

#### Removed (~500 lines)

- Constants: `FAMILY_ORDER`, `FAMILY_SPECS`, all `_FALLBACK_OPENERS`, `_FALLBACK_SUPPORT_SENTENCES`, `_FALLBACK_BULLETS`, `_FALLBACK_DEFAULT_HASHTAGS`
- Patterns: `_BULLET_PATTERN`, `_NUMBERED_PATTERN`, `_SENTENCE_PATTERN`, `_CAPTION_ABBREVIATIONS`
- Functions (17 total):
  - `_split_sentences()`, `_choice_from_pool()`, `_normalize_hashtag_token()`, `_build_hashtags()`
  - `_compress_sentence()`, `_ensure_minimum_sentence()`, `_material_sentences()`
  - `_build_caption_material()`, `_pick_fact_sentence()`
  - `_build_short_paragraph_variant()`, `_build_medium_bullets_variant()`, `_build_long_structured_variant()`
  - `_repair_caption_bundle()`
  - `_paragraph_contains_structured_list()`, `_index_of_first_structured_paragraph()`
  - `_bullet_lines()`, `_numbered_lines()`
  - `_caption_variant_pool()`, `_dedupe_preserve_order()`
- Import: `sanitize_metadata_text` (no longer used)

#### Added

- Constants: `VARIANT_KEYS = ("curiosity", "personal", "provocative")`, `CAPTION_MIN_CHARS = 80`, `CAPTION_MAX_CHARS = 400`
- Function: `extract_script_hook()` — extracts first sentence of script as spoken hook

#### Rewritten

- `_MARKER_PATTERN` — regex updated from `short_paragraph|medium_bullets|long_structured` to `curiosity|personal|provocative`
- `_build_caption_prompt()` — now accepts `script_hook` and `research_facts` instead of `context`
- `validate_caption_variant()` — unified 80-400 char validation, required hashtags, configurable `max_overlap` threshold
- `validate_caption_bundle()` — uses `VARIANT_KEYS`, accepts `has_research_facts` to control overlap threshold
- `select_caption_variant_key()` — selects from `VARIANT_KEYS` (no more post-type pool)
- `_select_best_variant()` — uses `VARIANT_KEYS` instead of `FAMILY_ORDER`
- `generate_caption_bundle()` — accepts `research_facts`, removed `context`/`fallback_facts`/`allow_repair` params, no repair path
- `attach_caption_bundle()` — extracts `research_facts` from `strict_seed.facts`, removed `context` param and `derived_context` computation

#### Kept unchanged

- `_normalize_line_breaks()`, `_split_paragraphs()`, `_extract_hashtags()`, `_count_emojis()`
- `_script_overlap_ratio()`, `_looks_mixed_language()`, `_meaningful_title_tokens()`, `_caption_looks_like_title()`
- `_resolve_canonical_topic()`, `resolve_selected_caption()`, `_load_caption_prompt_template()`
- `_COMMON_ENGLISH_WORDS`, `_TITLE_STOPWORDS`, `_HASHTAG_PATTERN`, `_EMOJI_PATTERN`

---

### `app/features/topics/prompt_data/captions_prompt.txt` — Replaced entirely

**Before:** 3-format marker prompt asking for `[short_paragraph]`, `[medium_bullets]`, `[long_structured]` with template vars `{topic_title}`, `{post_type}`, `{script}`, `{context}`.

**After:** Emotional-angle prompt asking for `[curiosity]`, `[personal]`, `[provocative]` with template vars `{topic_title}`, `{post_type}`, `{script}`, `{script_hook}`, `{research_facts}`. Includes:
- Example output showing all 3 emotional angles with Hook→Body→CTA→Hashtags structure
- Per-section character guidance (hook <100, body <150, total 150-300)
- Explicit instruction to NOT repeat the script hook
- Instruction to use CONCRETE facts from research, not generic sentences
- Named emotional angles with descriptions

---

### `app/features/topics/handlers.py` — Minor changes

- Removed `context` parameter from `_attach_publish_captions()` function signature
- Removed `context=` argument from all 4 call sites (lines ~556, ~707, ~857, ~923)
- Net: -6 lines

---

### `tests/test_caption_generation.py` — Rewritten

**Before:** 618 lines, 26 tests referencing old family system
**After:** 310 lines, 26 tests (48 total across caption suites) covering new structure

Test coverage:
- Validation: accepts new keys, rejects old keys, rejects too short/long, rejects >1 emoji, rejects research leakage, rejects high script overlap, rejects missing hashtags
- Bundle validation: accepts 3 variants, accepts partial, raises on no valid variants
- Selection: deterministic hash selection across `VARIANT_KEYS`
- Script hook extraction: first sentence extraction, full short script
- Prompt building: new template vars present, old markers absent
- Parsing: new markers parse correctly
- End-to-end generation: full bundle with new structure, persistent failure, LLM error
- attach_caption_bundle: sets description/caption, overwrites preexisting
- resolve_selected_caption: prefers bundle, falls back correctly
- default_publish_caption: prefers caption_bundle

---

## Commits

| SHA | Message |
|-----|---------|
| `773394d` | refactor: replace caption prompt with emotional-angle variants |
| `706b922` | refactor: rewrite caption generation from 3-format families to emotional-angle variants |
| `270925a` | feat(captions): rewrite to Hook-Body-CTA with 3 emotional-angle variations |
| `bba1246` | refactor(captions): remove dead context parameter from caption pipeline |
| `1d89ae9` | chore: remove unused deepcopy import from caption tests |
| `427353a` | fix(captions): widen validation range to 120-400 chars for LLM tolerance |
| `560fc7c` | fix(captions): relax overlap threshold when no research facts available |

---

## Validation Tuning

During live integration testing with Gemini, two issues surfaced and were resolved:

### Issue 1: Char range too strict (150-300)

Gemini occasionally generated captions slightly outside the 150-300 range. Widened to 80-400. The prompt still guides toward 150-300 as the ideal.

### Issue 2: Script overlap too high without research facts

When `research_facts` is empty (e.g., lifestyle topics with short scripts), the LLM has only the script to draw from. Overlap ratios reached 0.77-1.0, causing ALL variants to fail the 0.55 threshold.

Solution: adaptive threshold — `0.55` when research facts are available, `0.85` when they're not. This keeps strict quality when the LLM has real material, but allows reasonable paraphrasing when it doesn't.

---

## Before/After Comparison

### Before (generic fallback caption)
```
CAPTION
Oft machen kleine Strukturen den Unterschied, wenn im Alltag viele Dinge
gleichzeitig laufen. Sprich konkrete Bedürfnisse früh an, damit Unterstützung
nicht vom Zufall abhängt.

Wer Hinweise, Kontakte und nächste Schritte griffbereit hat, reagiert im Alltag
ruhiger und sicherer. Halte wichtige Kontakte, Fristen oder Hinweise griffbereit,
damit du im Alltag ruhiger reagieren kannst.

1. Sprich konkrete Bedürfnisse früh an, damit Unterstützung nicht vom Zufall abhängt.
2. Plane kleine Puffer ein, damit Belastung nicht wächst, sobald etwas ungeplant dazwischenkommt.
3. Notiere Absprachen kurz und klar, damit Hilfe im Alltag verlässlich und nachvollziehbar bleibt.

#ÖpnvAlltag #Rampe #Rollstuhlplätze #Barrierefrei
```

**Problems:** Generic filler, no specific facts, no CTA, no curiosity gap, could be about anything.

### After (research-driven caption with Hook→Body→CTA)
```
Das sagt dir dein Verkehrsbetrieb nicht: Viele Kommunen nutzten jahrelang
Ausnahmen, um Barrierefreiheit zu umgehen. Ab 2026 ist damit Schluss.

Speicher dir das, bevor es untergeht.

#BarriereFreiheit #ÖPNV #Inklusion
```

**Improvements:** Specific fact from research, curiosity gap hook, clear CTA driving saves, CamelCase hashtags, complementary to script hook.

---

## Related Documents

- **Design spec:** `docs/superpowers/specs/2026-03-30-caption-generation-redesign.md`
- **Implementation plan:** `docs/superpowers/plans/2026-03-30-caption-generation-redesign.md`
- **Audit agent spec:** `AIUGC/auditagent.md` (aligned quality bar, no code coupling)
