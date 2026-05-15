# Script Generation Stress Test — Final Report

**Run:** `2026-05-09T21:13Z` · **Elapsed:** 207 s · **LLM calls:** 93 (real Vertex AI Gemini 2.5 Flash) · **DB writes:** 0 (`upsert_topic_script_variants` patched)

**Raw artefacts**
- Harness: [tests/stress_test_script_generation.py](tests/stress_test_script_generation.py)
- Per-shot data: [tasks/stress_test_20260509_211631.jsonl](tasks/stress_test_20260509_211631.jsonl)
- Auto-generated summary: [tasks/stress_test_20260509_211631.md](tasks/stress_test_20260509_211631.md)

---

## TL;DR

**33 of 93 generated scripts (35.5%) pass all quality validators.** Two-thirds of what `expand_topic_variants` produces violates the spec defined in `app/core/video_profiles.py` and `app/features/topics/topic_validation.py`. Most of these violations would slip into production today because the persistence gate at [app/features/topics/topic_validation.py:600](app/features/topics/topic_validation.py:600) only enforces *word count* — not sentence count, char limit, or metadata bleed.

There is also a real concurrency hazard: when 6+ requests fire simultaneously the underlying Vertex/HTTP-2 connection tears down and yields `RemoteProtocolError`. The system retries the same shot but loses the whole burst.

---

## Headline metrics

| Metric | Value |
|---|---|
| Total shots | 93 |
| Clean (passes every validator) | **33 (35.5%)** |
| Quality-violation rate | 38.7% (36/93) |
| Real-exception rate | 17.2% (16/93) — 8 HTTP/2, 6 envelope-failure, 1 LocalProtocol, 1 KeyError |
| Cross-shot duplicates (Jaccard ≥ 0.58, same tier) | **0** ✓ |

### Pass rate by tier

| Tier | Shots | Clean | Pass rate |
|---|---|---|---|
| 8s | 31 | 12 | 38.7% |
| 16s | 31 | 8 | **25.8% — worst** |
| 32s | 31 | 13 | 41.9% |

### Pass rate by phase

| Phase | Shots | Clean | Pass rate | Note |
|---|---|---|---|---|
| smoke (sequential) | 3 | 0 | 0% | Tiny sample |
| concurrency (6 parallel × 3 tiers, single batch) | 18 | 2 | **11.1%** | HTTP/2 cliff: 8/18 burst-killed |
| heavy (8 parallel × 3 rounds × 3 tiers) | 72 | 31 | 43.1% | Steady-state |

The concurrency phase is uniquely degraded because the 18 calls land in one wave; HTTP/2 connections die mid-stream. Heavy phase recovers because each new round opens a fresh connection pool.

### Latency (success-only)

| Tier | n | p50 | p95 | max |
|---|---|---|---|---|
| 8s | 12 | 10.9 s | 15.3 s | 15.3 s |
| 16s | 8 | 14.4 s | 16.2 s | 16.2 s |
| 32s | 13 | 11.7 s | 17.4 s | 17.4 s |

Latency is fine. The problem is correctness, not throughput.

---

## Critical findings (ranked by severity)

### 1. Sentence-count violations are systematic, not random — and they ship to prod today

Sentence count is the dominant quality failure: 16 + 13 = **29 of 36 quality issues** (81%) are sentence-count violations.

| Tier | Spec | Most common violation |
|---|---|---|
| 16s | 3–4 sentences (`prompt1_sentence_guidance="DREI oder VIER vollstaendige Saetze"`) | **15 shots produced exactly 2 sentences** |
| 32s | 4–6 sentences (`prompt1_sentence_guidance="VIER natuerliche Sprechbloecke"`) | **10 shots produced 7–8 sentences** |

The 16s "2-sentence" failure is striking: the LLM treats the spec as "make ~30 words" and packs them into two long sentences instead of three short ones. Examples:

- `Du weißt noch nicht, dass dein Staubsaugerroboter dein bester Freund wird? Er nimmt dir die körperliche Belastung der Bodenreinigung ab und steigert deine Autonomie sowie Sicherheit im eigenen Zuhause.` (29 words, 2 sentences — should be 3–4)
- `Alle reden über barrierefreie Umbauten, aber niemand über den wahren Hürdenlauf bis zur Finanzierung. Oft verzögern unklare Zuständigkeiten und komplizierte Anträge die nötige Unterstützung durch KfW 455 B oder Pflegekassenzuschüsse.` (30 words, 2 sentences)

**Why it matters:** [validate_pre_persistence_topic_payload](app/features/topics/topic_validation.py:504) only enforces word bounds (line 600). Sentence bounds are computed (`get_prompt1_sentence_bounds`) but never gated. Both these examples would be persisted as valid scripts.

**Fix:** add a sentence-count check to `validate_pre_persistence_topic_payload`:

```python
sent_count = count_spoken_sentences(script)
min_s, max_s = get_prompt1_sentence_bounds(target_length_tier)
if sent_count < min_s or sent_count > max_s:
    raise ValidationError("Script failed sentence-count envelope", details={...})
```

Combine with a single LLM repair attempt mirroring the existing word-count repair (lines 549–598).

---

### 2. The `validate_pre_persistence_topic_payload` repair path *causes* metadata bleed

Of the 12 metadata-bleed hits, **at least 5 trace back to the repair-suffix logic** rather than the LLM. The repair appends `addon_source` (which is `source_summary` per [line 550](app/features/topics/topic_validation.py:550)) onto short scripts, then `detect_metadata_bleed` flags the result for being too verbatim.

Smoking-gun example (8s, smoke phase):

> Topic title: `Gute Terminwege barrierefreie Arzttermine Alltag Praxis Rückruf Support`
> Generated script: `Gute Terminwege barrierefreie Arzttermine Alltag Praxis Rückruf Support ganz konkret im Alltag für dich.`

That's literally the topic title + generic filler. The LLM under-produced; repair grabbed the title (which is also in `source_summary`); the result is a content-free string that nominally hits 14 words but is unusable as UGC. **5 different shots produced near-identical outputs of this shape**, all on topics whose title closely matches their source_summary.

**Fix candidates:**
- **(a) Best:** drop the `source_summary` clause from the repair `addon_source`. Use only the safe generics (`ganz konkret`, `im Alltag`, …). The generics alone won't trigger `detect_metadata_bleed`.
- **(b) Second-best:** run `detect_metadata_bleed` *after* the repair and re-attempt or reject.
- **(c) Strategic:** add a one-shot LLM repair (regenerate with explicit "do not echo title/summary" guidance) instead of mechanical suffix-appending.

---

### 3. Char-count limits are not enforced at persistence either

15 shots exceeded `prompt1_max_chars_no_spaces` (90 / 220 / 430 per tier). The 32s tier is the worst — 6 shots with `chars_no_spaces` between 432 and 501.

Like sentence count, char count is in `DurationProfile` but never checked at the gate. Add to `validate_pre_persistence_topic_payload`:

```python
if len(script.replace(" ", "")) > profile.prompt1_max_chars_no_spaces:
    raise ValidationError(...)
```

Why this limit matters: it's the upper bound for what fits in the spoken-segment envelope at TTS speed. Scripts above it will desync from the underlying video.

---

### 4. Concurrency burst kills HTTP/2 connections (`RemoteProtocolError × 8`)

All 8 `RemoteProtocolError` failures fired in the *same* concurrency batch, all at 3.18–3.22 s (timestamps cluster within 50 ms). They all carry `last_stream_id:47` — they shared a single multiplexed connection that hit a server-side stream-cap and tore down.

This means **today, if 6 simultaneous batches each request a script, ~40% of those calls will fail outright**. Heavy-phase rounds (which open fresh connections per round) didn't see this — the issue is concentrated at the *first* burst from a cold pool.

Symptoms:
- `RemoteProtocolError: <ConnectionTerminated error_code:ErrorCodes.PROTOCOL_ERROR, last_stream_id:47>` × 8
- `LocalProtocolError: Received pseudo-header in trailer …` × 1 (HTTP/2 frame corruption mid-stream)

**Fix candidates** (in [app/adapters/llm_client.py](app/adapters/llm_client.py)):
- Add a per-process semaphore around `generate_gemini_text` that caps concurrent in-flight requests (start with `max=4`).
- Add an `httpx` retry on `RemoteProtocolError` and `LocalProtocolError` with a short backoff (50–250 ms) — the existing retry table at [llm_client.py:959](app/adapters/llm_client.py:959) only retries `429/5xx`.
- Pin a single shared `httpx.Client` (or `google.cloud.aiplatform` client) per process and rely on its connection pool — currently each call may open a fresh connection, fighting for h2 streams.

---

### 5. Six shots returned `generated=0` from `expand_topic_variants`

The function's exception handler at [variant_expansion.py:423](app/features/topics/variant_expansion.py:423) silently swallows internal errors (in this run, `Script failed tier envelope for 8s` raised by `validate_pre_persistence_topic_payload`). The caller sees `generated=0` with no error detail. That's a debuggability gap: you can't tell from the return whether the LLM failed, the validator rejected, or the slot was exhausted.

**Fix:** include a `failures: List[Dict]` in the result dict alongside `details`, capturing `(framework, hook_style, error_class, error_message)` for each catch.

---

## Per-tier deep dive

### 8s — 38.7% pass

Issue distribution:
- `char_count_no_spaces_exceeded`: 6 (the 90-char ceiling is tight for German)
- `metadata_bleed`: 3 (most via repair-suffix path)
- `sentence_count_too_high`: 3 (LLM produced 2 sentences when spec is 1)

Representative failure (`heavy_r1_t8_value_6c3521af_9219`):
- Topic: *Forschungsdossier Problemfeld schwere Schwingtüren Rollstuhlnutzende*
- Output: `Niemand redet darüber, aber diese "barrierefreien" Türen sind dein größter Kraftakt, besonders 2026, Problemfeld schwere Schwingtüren für Rollstuhlnutzende.`
- 18 words, 1 sentence, **139 chars** (max 90)

The 8s tier carries the most concentrated topic-title-echo failures because it's the tier most likely to hit the word-count repair path.

### 16s — 25.8% pass (the worst tier)

Issue distribution:
- `sentence_count_too_low`: 15 — model preference for two long German sentences
- `metadata_bleed`: 4
- `char_count_no_spaces_exceeded`: 3

This tier also has the lowest variance in its failure mode — almost every failure is the same one. Strong signal that the **PROMPT_1 prompt for 16s should explicitly demand a 3-sentence minimum**, not just "DREI oder VIER vollstaendige Saetze" in the guidance line. The current prompt apparently isn't holding the model.

### 32s — 41.9% pass

Issue distribution:
- `sentence_count_too_high`: 10 — model loves 7-sentence outputs
- `char_count_no_spaces_exceeded`: 6 — even the 430-char ceiling gets blown
- `metadata_bleed`: 5

32s also has interesting cases where char count is at 432–436 (just barely over). Worth tightening the prompt's explicit hard bound, but the more reliable fix is gating at validation time.

---

## What's NOT broken

- **No cross-shot duplicates**: 0 pairs ≥ 0.58 bigram-Jaccard across 33 successful generations on overlapping topics. The model produces varied output even when given the same dossier.
- **Latency is fine**: p95 ≤ 17.4s on all tiers, including under heavy load.
- **No DB pollution**: the patched-upsert design worked — no stress data landed in production. Verified `topic_scripts`/`topic_registry` untouched (the only writes the path can do are gated by the patch).
- **Vertex AI auth/init is solid**: zero auth or model-availability errors.
- **Pre-persistence validator catches the worst cases**: 6 shots that failed the word envelope were correctly blocked from "persisting" (would have been blocked in prod too).

---

## Recommendations, ranked by impact ÷ effort

| # | Action | Effort | Impact | File |
|---|--------|--------|--------|------|
| 1 | Add sentence-count check to `validate_pre_persistence_topic_payload` | S | **High** — closes the largest leak (29/36 quality issues) | [topic_validation.py:600](app/features/topics/topic_validation.py:600) |
| 2 | Add char-count check to `validate_pre_persistence_topic_payload` | S | High — closes 15/36 issues | [topic_validation.py:600](app/features/topics/topic_validation.py:600) |
| 3 | Drop `source_summary`/`caption` from the repair `addon_source`; keep only safe generics | XS | Medium — kills 5+ bleed cases | [topic_validation.py:550](app/features/topics/topic_validation.py:550) |
| 4 | Add a process-level semaphore (max 4 concurrent) around `generate_gemini_text` | S | High — kills HTTP/2 burst error class | [llm_client.py:521](app/adapters/llm_client.py:521) |
| 5 | Retry on `RemoteProtocolError`/`LocalProtocolError` with short backoff | XS | Medium — papers over the few that slip past the semaphore | [llm_client.py:959](app/adapters/llm_client.py:959) |
| 6 | Tighten 16s PROMPT_1 prompt to require ≥ 3 sentences explicitly (not just "DREI oder VIER" in guidance) | S | Medium — addresses the root cause for 15 issues | [app/features/topics/prompts.py](app/features/topics/prompts.py) |
| 7 | Tighten 32s PROMPT_1 prompt to enforce ≤ 6 sentences | S | Medium — reduces model overshoot | [app/features/topics/prompts.py](app/features/topics/prompts.py) |
| 8 | Surface internal failures in `expand_topic_variants` return value (`failures: [...]`) | S | Low (debuggability) | [variant_expansion.py:423](app/features/topics/variant_expansion.py:423) |
| 9 | Re-run this stress test after #1–#5 land; expect ≥ 80% clean | S | n/a | re-run `tests/stress_test_script_generation.py` |

A pragmatic combo of #1+#2+#3+#4 should lift the pass rate from 35.5% to ~80%+ before touching prompt-level changes.

---

## How to reproduce

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && \
  set -a && . ./.env && set +a && \
  /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.venv/bin/python \
    /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.claude/worktrees/crazy-sinoussi-264e6f/tests/stress_test_script_generation.py \
    --phases smoke,concurrency,heavy \
    --concurrency-parallel 6 \
    --heavy-parallel 8 \
    --heavy-rounds 3 \
    --topic-pool-size 60 \
    --seed 7
```

The harness:
- Reads real `topic_registry` + `topic_research_dossiers` from the prod Supabase.
- Calls `expand_topic_variants` with the **real** Vertex AI Gemini client (one call per shot).
- Patches `upsert_topic_script_variants` (in `app.features.topics.variant_expansion`) so no stress data is ever written to `topic_scripts`. Captures the would-be variant via a thread-local sink so the script body can be validated.
- Runs three phases (smoke / concurrency / heavy) with a `ThreadPoolExecutor` keyed off `--*-parallel` flags.
- Validates each output against `DurationProfile` bounds + `detect_metadata_bleed` + `detect_spoken_copy_issues` + terminal-punctuation + sentence count.
- Writes the JSONL + auto-summary into `tasks/stress_test_<ts>.{jsonl,md}` and prints a verdict with non-zero exit if pass rate < 95%.

Use `--seed N` for reproducibility, `--topic-pool-size N` to widen sampling, and `--phases smoke` for a cheap (~3-call) check.
