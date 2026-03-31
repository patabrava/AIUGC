# Hook Engagement Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Overhaul hook bank and Stage 3 prompt instructions so generated scripts maximize TikTok engagement for German wheelchair-user audiences, replacing passive informational hooks with high-arousal, scroll-stopping patterns grounded in disability niche research.

**Architecture:** The hook bank YAML grows from 6 to 14 families, gains priority tiers with emotional-core matching metadata, disability-niche hook patterns, and before/after examples. All three Stage 3 prompts (8s/16s/32s) get rewritten hook mechanics and a disability-specific tone section replacing the single vague line. The `_format_hook_bank_section()` formatter renders the richer structure with priority ordering. The PROMPT_2 `hook_prefixes` tuple in response_parsers.py gains starters from new families. Existing tests are updated to cover new families and banned patterns.

**Tech Stack:** Python 3.11, YAML, plain-text prompt templates, pytest

**Research basis:** TikTok hook engagement data, German market cultural research (Advertace, Jim&Jim, DW case study), disability niche creator analysis (Krauthausen, Tan Caglar, Sabrina Lorenz, @trainierdichbehindert).

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `app/features/topics/prompt_data/hook_bank.yaml` | Hook families, banned patterns, negative examples | Full rewrite |
| `app/features/topics/prompt_data/prompt1_8s.txt` | Stage 3 prompt for 8s tier | Rewrite hook section + add tone/disability guard rails |
| `app/features/topics/prompt_data/prompt1_16s.txt` | Stage 3 prompt for 16s tier | Same rewrite adapted for 2-sentence format |
| `app/features/topics/prompt_data/prompt1_32s.txt` | Stage 3 prompt for 32s tier | Same rewrite adapted for 3-4 sentence format |
| `app/features/topics/prompts.py:274-291` | `_format_hook_bank_section()` formatter | Update to render priority tiers + negative examples |
| `app/features/topics/response_parsers.py:193-202` | `hook_prefixes` tuple for PROMPT_2 parsing | Extend with new family starters |
| `tests/test_prompt1_variant.py` | Hook bank structure tests | Add new assertions |
| `tests/test_topic_prompt_templates.py` | Prompt template content tests | Add hook mechanics assertions |

---

### Task 1: Overhaul `hook_bank.yaml`

**Files:**
- Modify: `app/features/topics/prompt_data/hook_bank.yaml`
- Test: `tests/test_prompt1_variant.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompt1_variant.py`:

```python
from app.features.topics.prompts import get_hook_bank


def test_hook_bank_has_high_engagement_families():
    """Hook bank must include high-engagement families from the overhaul."""
    bank = get_hook_bank()
    family_names = [f["name"] for f in bank["families"]]
    required = [
        "Identitaet und Zugehoerigkeit",
        "Provokation und Faktenkonflikt",
        "Zahlen und Spezifitaet",
        "Absurditaet und Realitaetscheck",
        "Neugier und Alltagsfragen",
        "Fehler und Warnung",
    ]
    for name in required:
        assert name in family_names, f"Missing high-engagement family: {name}"


def test_hook_bank_bans_weak_starters():
    """Weak starters must be in the banned list."""
    bank = get_hook_bank()
    banned = bank["banned_patterns"]
    assert any("Heute erklaere ich" in b for b in banned)
    assert any("In diesem Video" in b for b in banned)
    assert any("Ich moechte euch zeigen" in b for b in banned)


def test_hook_bank_families_have_priority():
    """Every family must declare a priority tier."""
    bank = get_hook_bank()
    for family in bank["families"]:
        assert "priority" in family, f"Family '{family['name']}' missing priority"
        assert family["priority"] in ("high", "medium", "low"), (
            f"Family '{family['name']}' has invalid priority: {family['priority']}"
        )


def test_hook_bank_has_negative_examples():
    """Hook bank must include before/after negative examples."""
    bank = get_hook_bank()
    examples = bank.get("negative_examples", [])
    assert len(examples) >= 3, "Need at least 3 negative examples"
    for ex in examples:
        assert "bad" in ex, "Negative example missing 'bad' key"
        assert "good" in ex, "Negative example missing 'good' key"
        assert "why" in ex, "Negative example missing 'why' key"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd AIUGC && python -m pytest tests/test_prompt1_variant.py -v`
Expected: 4 new tests FAIL (missing families, missing banned patterns, missing priority field, missing negative examples)

- [ ] **Step 3: Rewrite `hook_bank.yaml`**

Replace the entire file content of `app/features/topics/prompt_data/hook_bank.yaml` with:

```yaml
source_file: prompt1_8s.txt

families:
# --- HIGH PRIORITY: proven scroll-stoppers ---
- name: Kontrast- und Mythos-Hooks
  priority: high
  emotional_core: Unrecht, Versagen, Widerspruch
  examples:
  - "Die groesste Luege ueber ... ist, dass ..."
  - "Fast alle denken, ... — stimmt aber nicht."
  - "Alle reden ueber ..., aber keiner ueber ..."
  - "Auf dem Papier hast du das Recht. In der Realitaet? Fehlanzeige."

- name: Provokation und Faktenkonflikt
  priority: high
  emotional_core: Empoerung, Widerspruch — immer mit Faktenbeleg
  examples:
  - "Das sagt dir dein Verkehrsbetrieb nicht, aber ..."
  - "Hoert auf zu behaupten, dass ... — die Zahlen sagen was anderes."
  - "Sorry, aber ... ist einfach falsch."
  - "Niemand redet darueber, aber ..."

- name: Identitaet und Zugehoerigkeit
  priority: high
  emotional_core: Wiedererkennung, persoenliche Betroffenheit
  examples:
  - "Als Rollstuhlnutzer:in kennst du das ..."
  - "Das betrifft dich direkt, wenn du ..."
  - "Du kennst dieses Gefuehl, wenn ..."
  - "Jeder, der ... nutzt, weiss genau ..."

- name: Zahlen und Spezifitaet
  priority: high
  emotional_core: Ueberraschung, Faktenschock — konkrete Zahl im ersten Atemzug
  examples:
  - "Nur 2 Prozent aller Wohnungen in Deutschland sind rollstuhlgerecht."
  - "3 Sekunden — so lange wartest du, bis ..."
  - "Nur jede fuenfte Haltestelle ist tatsaechlich barrierefrei."
  - "Eine einzige Zahl zeigt, warum ..."

- name: Konsequenz- und Friktions-Hooks
  priority: high
  emotional_core: Dringlichkeit, Handlungsdruck
  examples:
  - "Wenn du ... ignorierst, passiert genau das."
  - "Der unangenehme Grund, warum ..."
  - "Dieser kleine Fehler macht deinen Alltag unnoetig schwer."
  - "Was passiert, wenn du ... einfach nicht bekommst?"

- name: Neugier und Alltagsfragen
  priority: high
  emotional_core: Echte Neugier ueber Alltag mit Rollstuhl
  examples:
  - "Wie kommt man als Rollstuhlfahrer:in eigentlich ins Flugzeug?"
  - "Was passiert, wenn die Rampe am Bus fehlt?"
  - "Wie funktioniert eigentlich ... im Rollstuhl?"
  - "Was machst du, wenn der Aufzug kaputt ist?"

- name: Fehler und Warnung
  priority: high
  emotional_core: Verlustangst — staerker als Gewinnversprechen
  examples:
  - "Dieser Fehler kostet dich als Rollstuhlfahrer:in Zeit und Nerven."
  - "Hoer auf mit ..., wenn du ... willst."
  - "Mach diesen Fehler nicht, wenn du ..."
  - "3 Dinge, die du bei ... falsch machst."

# --- MEDIUM PRIORITY: solid performers ---
- name: Absurditaet und Realitaetscheck
  priority: medium
  emotional_core: Sarkasmus, Ironie, Systemversagen
  examples:
  - "2022 sollte alles barrierefrei sein. Spoiler: ist es nicht."
  - "Was das Gesetz sagt — und was du an der Haltestelle erlebst."
  - "Deutschland 2025. Und du kommst trotzdem nicht in den Bus."

- name: Aha- und Handlungs-Hooks
  priority: medium
  emotional_core: Neugier, Aktivierung, Aha-Moment
  examples:
  - "Bevor du ... machst, solltest du das wissen."
  - "Alles veraendert sich, wenn du ..."
  - "Was dir bei ... niemand klar sagt: ..."
  - "3 Tipps, die ich gerne frueher gewusst haette."

- name: Empathische Hooks (Situation, nicht Mitleid)
  priority: medium
  emotional_core: Mitgefuehl mit der Situation, NICHT mit der Behinderung
  examples:
  - "Stell dir vor, du willst einfach nur in den Bus einsteigen."
  - "Kennst du das Gefuehl, wenn alles an einer fehlenden Rampe scheitert?"
  - "Ich zeig dir, was wirklich hilft bei ..."

- name: Direkte Aussagen
  priority: medium
  emotional_core: Handlung, Empowerment, Tipp
  examples:
  - "Check mal, was sich bei ... geaendert hat."
  - "Das musst du wissen, bevor du ..."
  - "Hier ist, was du tun kannst."

- name: Kurveneffekt (Alle betrifft's)
  priority: medium
  emotional_core: Reichweite ueber die Nische hinaus — Barrierefreiheit nuetzt allen
  examples:
  - "Das betrifft nicht nur Rollstuhlfahrer:innen — auch Eltern mit Kinderwagen."
  - "Barrierefreie Haltestellen helfen dir auch, wenn du verletzt bist."
  - "Warum gutes Design fuer alle funktioniert, nicht nur fuer wenige."

# --- LOW PRIORITY: use sparingly ---
- name: Fragen (nur mit Punch und konkreter Zahl)
  priority: low
  emotional_core: Neugier — nur mit schockierendem Fakt oder konkreter Zahl
  examples:
  - "Warum ist ... in Deutschland immer noch so schwer?"
  - "Wie kann es sein, dass ... im Jahr 2025 noch fehlt?"
  - "Wusstest du, dass nur 2 Prozent aller Wohnungen rollstuhlgerecht sind?"

- name: POV-Hooks
  priority: low
  emotional_core: Perspektivwechsel, Szenario
  examples:
  - "POV: Du willst einfach nur in den Bus einsteigen."
  - "POV: Du brauchst eine barrierefreie Wohnung in Deutschland."

banned_patterns:
- "Du wirst nicht glauben ..."
- "Ab 2025 gibt's..."
- "Es gibt..."
- "Heute erklaere ich..."
- "In diesem Video..."
- "Ich moechte euch zeigen..."
- "Lass uns mal ueber ... reden"
- "Hallo zusammen, heute..."
- "Willkommen zu meinem..."

negative_examples:
- bad: "Hast du gewusst, dass die Barrierefreiheit im OEPNV trotz gesetzlicher Frist 2022 oft noch fehlt?"
  good: "Du stehst an der Haltestelle — und der Bus faehrt einfach vorbei, weil die Rampe fehlt."
  why: "'Hast du gewusst' ohne Zahl ist Lehrer-Energie. Stattdessen die Zuhoererin IN die Szene setzen."
- bad: "Weisst du, welche Rechte du als Rollstuhlnutzer hast?"
  good: "Dein Recht auf Mitfahrt? Existiert auf dem Papier — nicht an der Haltestelle."
  why: "Abstrakte Frage vs. Kontrast zwischen Anspruch und Realitaet."
- bad: "Kennst du die Probleme mit barrierefreiem Wohnen?"
  good: "Nur 2 Prozent aller Wohnungen in Deutschland sind rollstuhlgerecht."
  why: "Abstrakte Frage vs. konkreter Faktenschock mit Zahl."
- bad: "Es gibt viele Barrieren im Alltag fuer Rollstuhlfahrer."
  good: "Deutschland 2025 — und du kommst trotzdem nicht in den Bus."
  why: "'Es gibt' ist passiv und langweilig. Stattdessen Realitaetscheck mit Jahreszahl."
```

- [ ] **Step 4: Clear lru_cache and run tests**

The `get_hook_bank()` and `_load_hook_bank_payload()` use `@lru_cache`. Tests need cache clearing. Add this fixture at the top of `tests/test_prompt1_variant.py` (after imports, before first test):

```python
import pytest
from app.features.topics import prompts as _prompts_mod


@pytest.fixture(autouse=True)
def _clear_hook_bank_cache():
    """Clear cached hook bank so tests pick up YAML changes."""
    _prompts_mod._load_hook_bank_payload.cache_clear()
    _prompts_mod.get_hook_bank.cache_clear()
    yield
    _prompts_mod._load_hook_bank_payload.cache_clear()
    _prompts_mod.get_hook_bank.cache_clear()
```

Run: `cd AIUGC && python -m pytest tests/test_prompt1_variant.py -v`
Expected: All tests PASS including 4 new ones

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/prompt_data/hook_bank.yaml tests/test_prompt1_variant.py
git commit -m "feat(hooks): overhaul hook bank with 14 families, priorities, disability-niche patterns, and negative examples"
```

---

### Task 2: Rewrite hook section in `prompt1_8s.txt`

**Files:**
- Modify: `app/features/topics/prompt_data/prompt1_8s.txt`
- Test: `tests/test_topic_prompt_templates.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_topic_prompt_templates.py`:

```python
def test_prompt1_8s_contains_hook_mechanics():
    """8s prompt must contain explicit hook mechanics, not just 'klaren Hook-Start'."""
    prompt = build_prompt1(post_type="value", desired_topics=1)
    assert "klaren Hook-Start" not in prompt, "Old vague hook instruction still present"
    assert "HOOK-REGELN" in prompt
    assert "Scroll-Stopp" in prompt
    assert "TONALITAET" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd AIUGC && python -m pytest tests/test_topic_prompt_templates.py::test_prompt1_8s_contains_hook_mechanics -v`
Expected: FAIL

- [ ] **Step 3: Rewrite `prompt1_8s.txt`**

Replace the entire file content of `app/features/topics/prompt_data/prompt1_8s.txt` with:

```
PROMPT_1_STAGE3
Du schreibst GENAU {desired_topics} finalen Scripttext fuer eine bereits recherchierte Lane.
Dein einziger Job ist Script-Schreiben. Keine neue Recherche, keine neue Themenfindung, keine neue Lane.

Zielgruppe: Rollstuhlnutzer:innen in Deutschland.

{research_context_section}

{hook_bank_section}

AUFGABE:
- Bleibe strikt beim uebergebenen Lane-Winkel.
- Erfinde keine neuen Fakten, keine neuen Quellen und kein neues Thema.
- Nutze nur Informationen, die im Dossier-Kontext stehen oder direkt daraus ableitbar sind.
- Gib nur den gesprochenen Scripttext aus.
- Keine JSON-Objekte, keine Titel, keine Caption, keine Aufzaehlung, keine Labels.

TONALITAET:
- Locker-direkt, in du-Form, empowernd und idiomatisch auf Deutsch.
- Der Ton ist wie eine Freundin, die dir etwas Krasses erzaehlt — nicht wie eine Lehrerin.
- Zeige Barrieren, nicht Ueberwindung. Das Problem ist die Umgebung, nicht die Person.
- Keine Mitleids-Hooks, keine Inspirations-Narrative ("trotz Behinderung geschafft").
- Barrierefreiheit nuetzt allen — Eltern mit Kinderwagen, Aeltere, Verletzte. Nutze diesen Kurveneffekt, wenn es passt.
- Jede Behauptung muss durch den Dossier-Kontext gedeckt sein. Keine unbelegten Aussagen.

HOOK-REGELN (wichtigster Teil des Scripts):
- Die ersten 2-3 Woerter MUESSEN Aufmerksamkeit erzwingen: Wut, Ueberraschung, Wiedererkennung oder Neugier.
- NIEMALS mit abstrakten Nomen oder buerokratischen Begriffen beginnen.
- Starte mit "du", einer konkreten Situation, einer provokanten Behauptung oder einer Zahl.
- Der Hook muss so spezifisch sein, dass die Zielgruppe sofort denkt: "Das betrifft MICH."
- Bevorzuge kurze, punchige Woerter vor langen Komposita.
- Waehle die Hook-Familie passend zum emotionalen Kern des Themas:
  - Unrecht oder Versagen → Kontrast- oder Provokations-Hook
  - Praktischer Tipp → Handlungs-, Fehler- oder Konsequenz-Hook
  - Alltag mit Rollstuhl → Neugier- oder Identitaets-Hook
  - Ueberraschendes Faktum → Zahlen-Hook mit konkreter Zahl
  - Systemversagen → Absurditaets- oder Realitaetscheck-Hook

SCROLL-STOPP-TEST:
- Frage dich vor der Ausgabe: Wuerde eine Rollstuhlfahrerin, die durch TikTok scrollt, bei diesem Satz stoppen?
- Wenn der Satz auch in einem Schulbuch stehen koennte: zu langweilig. Umschreiben.
- Wenn die ersten 3 Woerter keine Emotion ausloesen: umschreiben.

SCRIPT-REGELN:
- `script` muss GENAU 12-15 Woerter haben.
- `script` muss genau EIN vollstaendiger Satz sein.
- `script` muss mit `.`, `!` oder `?` enden.
- `script` muss vollstaendig auf Deutsch sein.
- Kein Clickbait, keine Panikmache, keine Passiv-Starts wie `Ab 2025 gibt es`.
- Keine Zwischenueberschriften, keine Forschungsetiketten wie `Zentrale Erkenntnisse`, keine Zitate wie `[cite: 1]`.

ANTWORTFORMAT:
- Nur der Scripttext
- Keine Markdown-Fences
- Keine Kommentare
- Kein Zusatztext
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd AIUGC && python -m pytest tests/test_topic_prompt_templates.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/prompt_data/prompt1_8s.txt tests/test_topic_prompt_templates.py
git commit -m "feat(hooks): rewrite 8s prompt with hook mechanics, disability tone, and scroll-stop test"
```

---

### Task 3: Apply hook rewrite to `prompt1_16s.txt` and `prompt1_32s.txt`

**Files:**
- Modify: `app/features/topics/prompt_data/prompt1_16s.txt`
- Modify: `app/features/topics/prompt_data/prompt1_32s.txt`
- Test: `tests/test_topic_prompt_templates.py`

- [ ] **Step 1: Write failing tests for 16s and 32s**

Append to `tests/test_topic_prompt_templates.py`:

```python
def test_prompt1_16s_contains_hook_mechanics():
    """16s prompt must contain explicit hook mechanics."""
    prompt = build_prompt1(
        post_type="value", desired_topics=1, profile=get_duration_profile(16),
    )
    assert "klaren Hook" not in prompt, "Old vague hook instruction still present in 16s"
    assert "HOOK-REGELN" in prompt
    assert "Scroll-Stopp" in prompt
    assert "TONALITAET" in prompt


def test_prompt1_32s_contains_hook_mechanics():
    """32s prompt must contain explicit hook mechanics."""
    prompt = build_prompt1(
        post_type="value", desired_topics=1, profile=get_duration_profile(32),
    )
    assert "klaren Hook" not in prompt, "Old vague hook instruction still present in 32s"
    assert "HOOK-REGELN" in prompt
    assert "Scroll-Stopp" in prompt
    assert "TONALITAET" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd AIUGC && python -m pytest tests/test_topic_prompt_templates.py::test_prompt1_16s_contains_hook_mechanics tests/test_topic_prompt_templates.py::test_prompt1_32s_contains_hook_mechanics -v`
Expected: FAIL

- [ ] **Step 3: Rewrite `prompt1_16s.txt`**

Replace the entire file content of `app/features/topics/prompt_data/prompt1_16s.txt` with:

```
PROMPT_1_STAGE3
Du schreibst GENAU {desired_topics} finalen Scripttext fuer eine bereits recherchierte Lane.
Dein einziger Job ist Script-Schreiben. Keine neue Recherche, keine neue Themenfindung, keine neue Lane.

Zielgruppe: Rollstuhlnutzer:innen in Deutschland.

{research_context_section}

{hook_bank_section}

AUFGABE:
- Bleibe strikt beim uebergebenen Lane-Winkel.
- Erfinde keine neuen Fakten, keine neuen Quellen und kein neues Thema.
- Nutze nur Informationen, die im Dossier-Kontext stehen oder direkt daraus ableitbar sind.
- Gib nur den gesprochenen Scripttext aus.
- Keine JSON-Objekte, keine Titel, keine Caption, keine Aufzaehlung, keine Labels.

TONALITAET:
- Locker-direkt, in du-Form, empowernd und idiomatisch auf Deutsch.
- Der Ton ist wie eine Freundin, die dir etwas Krasses erzaehlt — nicht wie eine Lehrerin.
- Zeige Barrieren, nicht Ueberwindung. Das Problem ist die Umgebung, nicht die Person.
- Keine Mitleids-Hooks, keine Inspirations-Narrative ("trotz Behinderung geschafft").
- Barrierefreiheit nuetzt allen — Eltern mit Kinderwagen, Aeltere, Verletzte. Nutze diesen Kurveneffekt, wenn es passt.
- Jede Behauptung muss durch den Dossier-Kontext gedeckt sein. Keine unbelegten Aussagen.

HOOK-REGELN (wichtigster Teil des Scripts):
- Die ersten 2-3 Woerter MUESSEN Aufmerksamkeit erzwingen: Wut, Ueberraschung, Wiedererkennung oder Neugier.
- NIEMALS mit abstrakten Nomen oder buerokratischen Begriffen beginnen.
- Starte mit "du", einer konkreten Situation, einer provokanten Behauptung oder einer Zahl.
- Der Hook muss so spezifisch sein, dass die Zielgruppe sofort denkt: "Das betrifft MICH."
- Bevorzuge kurze, punchige Woerter vor langen Komposita.
- Waehle die Hook-Familie passend zum emotionalen Kern des Themas:
  - Unrecht oder Versagen → Kontrast- oder Provokations-Hook
  - Praktischer Tipp → Handlungs-, Fehler- oder Konsequenz-Hook
  - Alltag mit Rollstuhl → Neugier- oder Identitaets-Hook
  - Ueberraschendes Faktum → Zahlen-Hook mit konkreter Zahl
  - Systemversagen → Absurditaets- oder Realitaetscheck-Hook

SCROLL-STOPP-TEST:
- Frage dich vor der Ausgabe: Wuerde eine Rollstuhlfahrerin, die durch TikTok scrollt, bei diesem Satz stoppen?
- Wenn der erste Satz auch in einem Schulbuch stehen koennte: zu langweilig. Umschreiben.
- Wenn die ersten 3 Woerter keine Emotion ausloesen: umschreiben.

SCRIPT-REGELN:
- `script` muss GENAU 26-36 Woerter haben.
- Wenn `script` unter 26 oder ueber 36 Woerter liegt, ist die Antwort ungueltig.
- `script` muss genau ZWEI vollstaendige Saetze haben.
- Der erste Satz ist der Hook. Der zweite Satz liefert die konkrete Einordnung, Konsequenz oder den Nutzen.
- `script` muss mit `.`, `!` oder `?` enden.
- `script` muss vollstaendig auf Deutsch sein.
- Kein Clickbait, keine Panikmache, keine Passiv-Starts wie `Ab 2025 gibt es`.
- Keine Zwischenueberschriften, keine Forschungsetiketten wie `Zentrale Erkenntnisse`, keine Zitate wie `[cite: 1]`.

ANTWORTFORMAT:
- Nur der Scripttext
- Keine Markdown-Fences
- Keine Kommentare
- Kein Zusatztext
```

- [ ] **Step 4: Rewrite `prompt1_32s.txt`**

Replace the entire file content of `app/features/topics/prompt_data/prompt1_32s.txt` with:

```
PROMPT_1_STAGE3
Du schreibst GENAU {desired_topics} finalen Scripttext fuer eine bereits recherchierte Lane.
Dein einziger Job ist Script-Schreiben. Keine neue Recherche, keine neue Themenfindung, keine neue Lane.

Zielgruppe: Rollstuhlnutzer:innen in Deutschland.

{research_context_section}

{hook_bank_section}

AUFGABE:
- Bleibe strikt beim uebergebenen Lane-Winkel.
- Erfinde keine neuen Fakten, keine neuen Quellen und kein neues Thema.
- Nutze nur Informationen, die im Dossier-Kontext stehen oder direkt daraus ableitbar sind.
- Gib nur den gesprochenen Scripttext aus.
- Keine JSON-Objekte, keine Titel, keine Caption, keine Aufzaehlung, keine Labels.

TONALITAET:
- Locker-direkt, in du-Form, empowernd und idiomatisch auf Deutsch.
- Der Ton ist wie eine Freundin, die dir etwas Krasses erzaehlt — nicht wie eine Lehrerin.
- Zeige Barrieren, nicht Ueberwindung. Das Problem ist die Umgebung, nicht die Person.
- Keine Mitleids-Hooks, keine Inspirations-Narrative ("trotz Behinderung geschafft").
- Barrierefreiheit nuetzt allen — Eltern mit Kinderwagen, Aeltere, Verletzte. Nutze diesen Kurveneffekt, wenn es passt.
- Jede Behauptung muss durch den Dossier-Kontext gedeckt sein. Keine unbelegten Aussagen.

HOOK-REGELN (wichtigster Teil des Scripts):
- Die ersten 2-3 Woerter MUESSEN Aufmerksamkeit erzwingen: Wut, Ueberraschung, Wiedererkennung oder Neugier.
- NIEMALS mit abstrakten Nomen oder buerokratischen Begriffen beginnen.
- Starte mit "du", einer konkreten Situation, einer provokanten Behauptung oder einer Zahl.
- Der Hook muss so spezifisch sein, dass die Zielgruppe sofort denkt: "Das betrifft MICH."
- Bevorzuge kurze, punchige Woerter vor langen Komposita.
- Waehle die Hook-Familie passend zum emotionalen Kern des Themas:
  - Unrecht oder Versagen → Kontrast- oder Provokations-Hook
  - Praktischer Tipp → Handlungs-, Fehler- oder Konsequenz-Hook
  - Alltag mit Rollstuhl → Neugier- oder Identitaets-Hook
  - Ueberraschendes Faktum → Zahlen-Hook mit konkreter Zahl
  - Systemversagen → Absurditaets- oder Realitaetscheck-Hook

SCROLL-STOPP-TEST:
- Frage dich vor der Ausgabe: Wuerde eine Rollstuhlfahrerin, die durch TikTok scrollt, bei diesem Satz stoppen?
- Wenn der erste Satz auch in einem Schulbuch stehen koennte: zu langweilig. Umschreiben.
- Wenn die ersten 3 Woerter keine Emotion ausloesen: umschreiben.

SCRIPT-REGELN:
- `script` muss GENAU 54-74 Woerter haben.
- Wenn `script` unter 54 oder ueber 74 Woerter liegt, ist die Antwort ungueltig.
- `script` muss genau DREI oder VIER vollstaendige Saetze haben.
- Der erste Satz ist der Hook. Die folgenden Saetze liefern konkrete Einordnung, Konsequenz und Nutzen.
- `script` muss mit `.`, `!` oder `?` enden.
- `script` muss vollstaendig auf Deutsch sein.
- Kein Clickbait, keine Panikmache, keine Passiv-Starts wie `Ab 2025 gibt es`.
- Keine Zwischenueberschriften, keine Forschungsetiketten wie `Zentrale Erkenntnisse`, keine Zitate wie `[cite: 1]`.

ANTWORTFORMAT:
- Nur der Scripttext
- Keine Markdown-Fences
- Keine Kommentare
- Kein Zusatztext
```

- [ ] **Step 5: Run all prompt template tests**

Run: `cd AIUGC && python -m pytest tests/test_topic_prompt_templates.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/features/topics/prompt_data/prompt1_16s.txt app/features/topics/prompt_data/prompt1_32s.txt tests/test_topic_prompt_templates.py
git commit -m "feat(hooks): apply hook mechanics and disability tone to 16s and 32s prompts"
```

---

### Task 4: Update `_format_hook_bank_section()` to render new structure

**Files:**
- Modify: `app/features/topics/prompts.py:274-291`
- Test: `tests/test_prompt1_variant.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompt1_variant.py`:

```python
def test_format_hook_bank_includes_priority_ordering():
    """High-priority families must appear before low-priority in rendered output."""
    prompt = build_prompt1(post_type="value", desired_topics=1)
    high_pos = prompt.find("Provokation und Faktenkonflikt")
    low_pos = prompt.find("Fragen (nur mit Punch")
    assert high_pos != -1, "High-priority family not found in prompt"
    assert low_pos != -1, "Low-priority family not found in prompt"
    assert high_pos < low_pos, "High-priority families must appear before low-priority"


def test_format_hook_bank_includes_negative_examples():
    """Rendered hook bank must include before/after examples."""
    prompt = build_prompt1(post_type="value", desired_topics=1)
    assert "SCHLECHT:" in prompt
    assert "GUT:" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd AIUGC && python -m pytest tests/test_prompt1_variant.py::test_format_hook_bank_includes_priority_ordering tests/test_prompt1_variant.py::test_format_hook_bank_includes_negative_examples -v`
Expected: FAIL

- [ ] **Step 3: Update `_format_hook_bank_section()` in `prompts.py`**

Replace lines 274-291 of `app/features/topics/prompts.py` (the `_format_hook_bank_section` function) with:

```python
def _format_hook_bank_section() -> str:
    payload = get_hook_bank()
    families = list(payload.get("families") or [])
    banned = [str(item).strip() for item in list(payload.get("banned_patterns") or []) if str(item).strip()]
    negative_examples = list(payload.get("negative_examples") or [])
    if not families and not banned:
        return ""

    priority_order = {"high": 0, "medium": 1, "low": 2}
    sorted_families = sorted(
        families,
        key=lambda f: priority_order.get(str(f.get("priority", "medium")), 1),
    )

    lines = ["HOOK-BANK (verbindlich):"]
    current_priority = None
    for family in sorted_families:
        name = str(family.get("name") or "").strip()
        examples = [str(item).strip() for item in list(family.get("examples") or []) if str(item).strip()]
        priority = str(family.get("priority", "medium"))
        if not name or not examples:
            continue
        if priority != current_priority:
            label = {"high": "BEVORZUGT", "medium": "SOLIDE", "low": "SPARSAM EINSETZEN"}.get(priority, "")
            if label:
                lines.append(f"\n[{label}]")
            current_priority = priority
        lines.append(f"- {name}: " + ", ".join(f'"{example}"' for example in examples))

    if banned:
        lines.append("\nVerbotene Hook-Starter (NIEMALS verwenden):")
        lines.extend(f"- {item}" for item in banned)

    if negative_examples:
        lines.append("\nBeispiele (vorher/nachher):")
        for ex in negative_examples:
            bad = str(ex.get("bad", "")).strip()
            good = str(ex.get("good", "")).strip()
            why = str(ex.get("why", "")).strip()
            if bad and good:
                lines.append(f'SCHLECHT: "{bad}"')
                lines.append(f'GUT: "{good}"')
                if why:
                    lines.append(f"Warum: {why}")
                lines.append("")

    return "\n".join(lines).strip()
```

- [ ] **Step 4: Run tests**

Run: `cd AIUGC && python -m pytest tests/test_prompt1_variant.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/prompts.py tests/test_prompt1_variant.py
git commit -m "feat(hooks): update formatter to render priority tiers and negative examples"
```

---

### Task 5: Update `hook_prefixes` in `response_parsers.py`

**Files:**
- Modify: `app/features/topics/response_parsers.py:193-202`
- Test: `tests/test_topic_prompt_templates.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_topic_prompt_templates.py`:

```python
def test_prompt2_hook_prefixes_include_new_families():
    """PROMPT_2 parser must recognize hooks from new bank families."""
    from app.features.topics.response_parsers import parse_prompt2_response

    raw = """## Problem-Agitieren-Lösung Ads
Das sagt dir dein Verkehrsbetrieb nicht, aber die Rampe fehlt seit Monaten.
Dieser Fehler kostet dich als Rollstuhlfahrerin Zeit und Nerven jeden Tag.
Nur 2 Prozent aller Wohnungen sind rollstuhlgerecht in ganz Deutschland.

## Testimonial Ads
Wie kommt man als Rollstuhlfahrerin eigentlich ins Flugzeug ohne fremde Hilfe?
Deutschland 2025 und du kommst trotzdem nicht in den verdammten Bus.
Als Rollstuhlnutzerin kennst du das Gefuehl wenn der Aufzug kaputt ist.

## Transformation Ads
Dein Recht auf Mitfahrt existiert auf dem Papier aber nicht an der Haltestelle.
POV: Du willst einfach nur in den Bus einsteigen und es geht nicht.
Warum ist Barrierefreiheit in Deutschland immer noch so verdammt schwer?
"""
    result = parse_prompt2_response(raw, max_per_category=5)
    assert len(result.problem_agitate_solution) >= 1
    assert len(result.testimonial) >= 1
    assert len(result.transformation) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd AIUGC && python -m pytest tests/test_topic_prompt_templates.py::test_prompt2_hook_prefixes_include_new_families -v`
Expected: FAIL — new hook starters not recognized

- [ ] **Step 3: Update `hook_prefixes` tuple**

In `app/features/topics/response_parsers.py`, replace lines 193-202 (the `hook_prefixes` tuple inside `parse_prompt2_response`) with:

```python
    hook_prefixes = (
        # Original families
        "kennst du", "weißt du", "hast du", "brauchst du", "suchst du", "check mal", "schau dir",
        "hier kommt", "das musst", "stell dir", "ich zeig", "lass mich", "die größte", "wenn du",
        "fast alle", "der unangenehme", "die meisten", "was dir", "bevor du", "alles verändert",
        "dieser kleine", "viele verlassen", "alle reden", "die harte", "schon mal erlebt",
        "ich dachte früher", "ich dachte lange", "neulich ist mir", "wusstest du", "manchmal frage ich",
        "ehrlich gesagt", "von außen", "was viele", "kaum jemand", "dieser eine", "niemand sagt",
        "alle denken", "was meinen alltag", "seit ich", "viele meinen", "der moment", "erst wenn",
        "das frustigste", "eine sache",
        # Provokation und Faktenkonflikt
        "das sagt dir", "hoert auf", "hört auf", "sorry, aber", "niemand redet",
        # Identitaet und Zugehoerigkeit
        "als rollstuhlnutzer", "das betrifft dich", "du kennst dieses", "jeder, der",
        "wenn du schon mal",
        # Zahlen und Spezifitaet
        "nur 2 prozent", "nur jede", "eine einzige zahl", "3 sekunden",
        # Neugier und Alltagsfragen
        "wie kommt man", "was passiert, wenn", "wie funktioniert", "was machst du",
        # Fehler und Warnung
        "dieser fehler", "hoer auf mit", "hör auf mit", "mach diesen fehler",
        "3 dinge, die du",
        # Absurditaet und Realitaetscheck
        "was das gesetz", "deutschland 2025", "deutschland 2026", "2022 sollte",
        # Kurveneffekt
        "das betrifft nicht nur", "barrierefreie",
        # POV
        "pov:",
        # Fragen mit Punch
        "warum ist", "wie kann es sein",
        # Kontrast shorthand
        "dein recht", "auf dem papier",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd AIUGC && python -m pytest tests/test_topic_prompt_templates.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/response_parsers.py tests/test_topic_prompt_templates.py
git commit -m "feat(hooks): extend prompt2 hook_prefixes with new family starters"
```

---

### Task 6: Update existing test assertions

**Files:**
- Modify: `tests/test_prompt1_variant.py`
- Modify: `tests/test_topic_prompt_templates.py`

- [ ] **Step 1: Update `test_build_prompt1_includes_yaml_hook_bank_for_canonical_path`**

In `tests/test_prompt1_variant.py`, replace lines 29-34 with:

```python
def test_build_prompt1_includes_yaml_hook_bank_for_canonical_path():
    """Canonical PROMPT_1 must also inject the YAML hook bank."""
    prompt = build_prompt1(post_type="value", desired_topics=1)
    assert "HOOK-BANK" in prompt
    assert "Fragen" in prompt  # matches "Fragen (nur mit Punch und konkreter Zahl)"
    assert "Du wirst nicht glauben" in prompt  # still in banned list
    assert "Heute erklaere ich" in prompt  # new banned pattern
```

- [ ] **Step 2: Update `test_build_prompt1_uses_32s_text_template`**

In `tests/test_topic_prompt_templates.py`, the existing test at lines 29-62 asserts `"Fragen" in prompt` (line 59). The renamed family still contains "Fragen" so this assertion still holds. No change needed.

Verify: `"Starte mit einem klaren Hook"` is gone from the 32s prompt. The existing test does not assert this string, so no breakage.

- [ ] **Step 3: Run the full test suite**

Run: `cd AIUGC && python -m pytest tests/test_prompt1_variant.py tests/test_topic_prompt_templates.py tests/test_topic_pipeline.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_prompt1_variant.py tests/test_topic_prompt_templates.py
git commit -m "test(hooks): update existing assertions for renamed families and new banned patterns"
```

---

### Task 7: Run full regression suite and verify rendered output

**Files:**
- No modifications — verification only

- [ ] **Step 1: Run all topic-related tests**

Run: `cd AIUGC && python -m pytest tests/test_topic_prompt_templates.py tests/test_prompt1_variant.py tests/test_topic_pipeline.py tests/test_topics_gemini_flow.py tests/test_lifestyle_generation_regression.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run ruff linting**

Run: `cd AIUGC && ruff check app/features/topics/prompts.py app/features/topics/response_parsers.py`
Expected: No errors

- [ ] **Step 3: Sanity-check rendered prompt output**

Run:

```bash
cd AIUGC && python -c "
from app.features.topics.prompts import build_prompt1, get_hook_bank
# Clear cache first
from app.features.topics import prompts as _p
_p._load_hook_bank_payload.cache_clear()
_p.get_hook_bank.cache_clear()

prompt = build_prompt1(post_type='value', desired_topics=1)
print(prompt[:4000])
print('---')
print(f'Total length: {len(prompt)} chars')

bank = get_hook_bank()
print(f'Families: {len(bank[\"families\"])}')
print(f'Banned: {len(bank[\"banned_patterns\"])}')
print(f'Negative examples: {len(bank.get(\"negative_examples\", []))}')
for f in bank['families']:
    print(f'  [{f[\"priority\"]}] {f[\"name\"]}')
"
```

Verify output contains:
- `HOOK-REGELN` section with emotional-core matching
- `SCROLL-STOPP-TEST` section
- `TONALITAET` section with disability guard rails
- `[BEVORZUGT]` and `[SOLIDE]` and `[SPARSAM EINSETZEN]` priority labels
- `SCHLECHT:` / `GUT:` before/after examples
- 14 families listed with correct priority assignments
- 9 banned patterns
- 4 negative examples

- [ ] **Step 4: Update `deep-research-flow.md`**

Add a note to Stage 3 in `deep-research-flow.md` about the hook bank overhaul. Append after line 27 (after the cleanliness gate bullet):

```
- Hook bank (`app/features/topics/prompt_data/hook_bank.yaml`) provides 14 prioritized families with emotional-core matching; `_format_hook_bank_section()` renders them sorted by priority (high → medium → low) with before/after negative examples
- All three tier prompts include HOOK-REGELN (emotional hook mechanics), SCROLL-STOPP-TEST (self-evaluation), and TONALITAET (disability-appropriate tone: systemic barriers over personal overcoming, no inspiration porn)
```

- [ ] **Step 5: Commit**

```bash
git add deep-research-flow.md
git commit -m "docs: update deep-research-flow with hook bank overhaul details"
```
