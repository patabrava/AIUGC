# Caption Generation Redesign

## Problem

Caption generation produces generic, disconnected captions that have nothing to do with the actual script or research. The system falls back to ~150 lines of hardcoded German filler sentences instead of using real research data. Captions lack TikTok engagement strategy — no hooks, no CTAs, no curiosity gaps.

## Research Findings (2025-2026 Data)

### Winning Caption Structure
One structure consistently outperforms all others across TikTok and Instagram Reels:

```
[HOOK — <100 chars, keyword in first 30 chars]
[BODY — 1-2 sentences, specific fact, <150 chars]
[CTA — direct action ask: save/share/comment]
[3-5 CamelCase hashtags]
```

Total: 150-300 chars. This optimizes for the 150-char FYP truncation point, drives saves/shares via explicit CTA (top algorithm signal in 2026), and includes keywords for search discovery.

### Caption vs Script Hook Relationship
- Caption hook must COMPLEMENT the script hook, never repeat it
- Complementary hooks get 10-25% higher save rates vs identical hooks
- Verbatim repetition reduces watch time by 5-15%
- Different keywords across caption vs script expand algorithmic surface area
- Same topic, different emotional angle, additive keywords

### Key Engagement Data
- CTAs boost visibility by 55.7%
- Questions in captions drive 44% more comments
- Short captions (<100 chars visible) get 21% higher engagement
- Saves/shares are the #1 algorithm signal in 2026

## Design

### Single Structure, 3 Hook Variations

Replace the 3-format system (short_paragraph / medium_bullets / long_structured) with 3 emotional angle variations on the same Hook-Body-CTA structure:

1. **curiosity** — Social proof / curiosity gap angle
2. **personal** — Relatable / affected-person angle
3. **provocative** — Challenge / pattern interrupt angle

Example for "Barrierefreiheit im ÖPNV":
- curiosity: "Das verschweigt dir dein Verkehrsbetrieb seit Jahren."
- personal: "Wenn du ÖPNV fährst, betrifft dich das ab 2026."
- provocative: "Keine Rampe, kein Aufzug — und trotzdem 'barrierefrei'?"

Selection: deterministic hash picks 1 of 3 (same mechanism as current system).

### Data Flow

```
Research Dossier (facts, source_summary, lane_dossier)
        |
        v
Extract top 5 most specific/surprising facts
(cleaned of metadata residue, NOT over-sanitized)
        |
        v
Pass to LLM: research facts + script + script hook + topic title + post_type
        |
        v
LLM generates 3 caption variations using research facts
        |
        v
Validation (structure + topical alignment + no script repetition)
```

Research facts are passed as a numbered list — the LLM picks the most engaging ones. No more compressed context fragments.

Script hook extraction: take the first sentence of the script (split on `.!?`). This is the spoken hook the video opens with — the caption must not repeat it.

Fallback when research is thin: LLM uses the script itself as source material, reframes from a different angle. No hardcoded German fallback sentences.

### Prompt Template

```
Du bekommst ein Thema, ein Skript, den Skript-Hook und Recherche-Fakten.
Erstelle 3 Caption-Varianten mit UNTERSCHIEDLICHEN emotionalen Winkeln.

SKRIPT-HOOK (das sagt das Video am Anfang — NICHT wiederholen):
{script_hook}

RECHERCHE-FAKTEN (waehle die ueberraschendsten fuer den Body):
{research_facts}

THEMA: {topic_title}
POST-TYP: {post_type}
SKRIPT: {script}

JEDE Caption folgt dieser Struktur:
[HOOK] — Max 100 Zeichen. Hauptkeyword in den ersten 30 Zeichen.
         Anderer emotionaler Winkel als der Skript-Hook. Nie den Skript-Hook kopieren.

[BODY] — 1-2 Saetze, max 150 Zeichen. Nutze einen konkreten Fakt aus der Recherche.

[CTA] — Direkte Handlungsaufforderung (Speichern/Teilen/Kommentieren).

[HASHTAGS] — 3-5 thematische Hashtags in CamelCase.

VARIANTE 1: Neugier/Social-Proof-Winkel
VARIANTE 2: Persoenlicher/Betroffenheits-Winkel
VARIANTE 3: Provokativer/Herausforderungs-Winkel

Regeln:
- Jeder Hook muss einen anderen emotionalen Winkel haben
- Body muss einen KONKRETEN Fakt aus der Recherche enthalten, nicht generisch
- Caption darf den Skript-Hook NICHT woertlich wiederholen
- Gesamtlaenge pro Caption: 150-300 Zeichen
- Alles auf Deutsch, natuerlich gesprochen
- Max 1 Emoji pro Caption
```

### Validation Rules

| Check | Rule | Reason |
|---|---|---|
| Total length | 150-300 chars | Educational TikTok sweet spot |
| Hook length | <=100 chars, keyword in first 30 | Algorithm discovery + truncation |
| Has CTA | Last line matches CTA patterns | Drives saves/shares |
| Has hashtags | 3-5, CamelCase format | Discovery |
| Script overlap | Lexical overlap with script hook <=40% | Complementary, not repetitive |
| Topical alignment | Shares >=2 topical keywords with script | Same topic, different angle |
| Language | German-only, no English leakage | DACH algorithm targeting |
| Metadata clean | No research labels, citations, markdown | Integrity |

Retry: up to 3 attempts with error feedback appended. No silent fallback to hardcoded sentences — failures are visible.

### Pipeline Timing

Captions generate during batch seeding (S2_SEEDED), same as current behavior. No pipeline timing changes.

## Code Impact

### Files That Change

| File | Change |
|---|---|
| `app/features/topics/captions.py` | Major rewrite — remove ~300 lines of fallback/family machinery, new Hook-Body-CTA structure, new validation, new prompt builder |
| `app/features/topics/prompt_data/captions_prompt.txt` | Replace entirely with new prompt template |
| `app/features/topics/handlers.py` | Minor — pass script_hook and research_facts to caption generator |
| `app/features/topics/seed_builders.py` | Minor — ensure research facts available without over-sanitization |

### What Gets Deleted from captions.py

- `FAMILY_ORDER`, `FAMILY_SPECS` constants
- All `_FALLBACK_OPENERS`, `_FALLBACK_SUPPORT_SENTENCES`, `_FALLBACK_BULLETS` dictionaries (~100 lines)
- `_build_short_paragraph_variant()`, `_build_medium_bullets_variant()`, `_build_long_structured_variant()`
- `_build_caption_material()`, `_pick_fact_sentence()`, `_compress_sentence()`, `_ensure_minimum_sentence()`
- `_repair_caption_bundle()` and family-specific validation branches
- `_caption_variant_pool()`

### What Stays

- `_normalize_line_breaks()`, `_extract_hashtags()`, `_script_overlap_ratio()` — utility functions
- `_looks_mixed_language()`, `_caption_looks_like_title()` — valid quality checks
- `detect_metadata_copy_issues()` — integrity check
- `generate_caption_bundle()` — rewritten, same interface
- `attach_caption_bundle()` — updated to pass research facts and script hook
- `validate_caption_bundle()` — rewritten with new rules

### Output Contract (Backward Compatible)

```python
{
    "variants": [
        {"key": "curiosity", "body": "..."},
        {"key": "personal", "body": "..."},
        {"key": "provocative", "body": "..."},
    ],
    "selected_key": "curiosity",
    "selected_body": "Das verschweigt dir dein Verkehrsbetrieb...",
    "selection_reason": "hash_variant",
}
```

Keys change from short_paragraph/medium_bullets/long_structured to curiosity/personal/provocative. Bundle dict structure unchanged — downstream consumers (templates, API) unaffected.

## Audit Agent Alignment

The caption system and audit agent share the same quality bar:
- Hook quality: caption hook complements (not repeats) script hook
- Content quality: body contains specific research facts, not generic filler
- CTA present
- Natural German

Caption validation is the deterministic gate (runs first). Audit agent is the LLM-based second layer that catches what validation cannot (native-sounding German, genuine scroll-stopping hooks).

No coupling between the two systems.
