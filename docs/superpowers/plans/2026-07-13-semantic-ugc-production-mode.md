# Semantic UGC Production Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production `semantic_ugc` batch mode with arbitrary 8-60 second targets, dynamic scripts, immutable reference and cost approvals, resumable Veo 3.1 takes, per-take QA, captions, and one budget-capped live proof.

**Architecture:** Preserve every legacy duration route. The new mode uses a separate semantic duration contract, duration-neutral topic selection, just-in-time script generation, normalized run/take/approval persistence, and a dedicated idempotent worker. Existing shot-frame and shot-production primitives remain the generation and QA engine; the new feature slice supplies durable application orchestration.

**Tech Stack:** Python 3, FastAPI, Pydantic, Supabase/PostgreSQL, HTMX/Alpine, Vertex AI Veo 3.1, Gemini 3.1 Flash Image, Deepgram, FFmpeg, pytest.

---

## File map

- `app/features/shot_production/duration.py`: sole Semantic UGC duration, word, take-count, canonical-hash, and price contract.
- `app/features/topics/semantic_scripts.py`: generic duration-aware prompt rendering, response normalization, fallback construction, and script validation.
- `app/features/topics/prompt_data/semantic_{value,lifestyle,product}.txt`: post-family templates used only by Semantic UGC.
- `app/features/batches/{schemas,handlers,queries}.py`: form/API validation and batch persistence for the new mode.
- `templates/batches/list.html`: mode selector and numeric duration UX.
- `supabase/migrations/20260713_semantic_ugc_production.sql`: batch contract plus run/take/approval persistence and atomic lease helper.
- `app/features/semantic_videos/{schemas,queries,service,handlers}.py`: production orchestration vertical slice.
- `workers/semantic_video_worker.py`: one-stage-per-tick resumable worker.
- `templates/batches/detail/_semantic_video.html` and `static/js/batches/semantic_video.js`: reference, plan, approval, retry, and progress UI.
- `scripts/run_semantic_ugc_live_smoke.py`: one-request, hard-budget live proof harness.
- Focused tests under `tests/test_semantic_*.py`, plus legacy batch/topic/video regression tests.

### Task 1: Semantic duration and dynamic script contracts

**Files:**
- Create: `app/features/shot_production/duration.py`
- Create: `app/features/topics/semantic_scripts.py`
- Create: `app/features/topics/prompt_data/semantic_value.txt`
- Create: `app/features/topics/prompt_data/semantic_lifestyle.txt`
- Create: `app/features/topics/prompt_data/semantic_product.txt`
- Create: `tests/test_semantic_duration_contract.py`
- Create: `tests/test_semantic_scripts.py`

- [ ] **Step 1: Write the failing duration tests**

```python
import pytest

from app.features.shot_production.duration import build_semantic_duration_contract


@pytest.mark.parametrize(
    ("seconds", "takes", "minimum_words", "maximum_words"),
    [(8, 1, 14, 18), (16, 2, 29, 36), (32, 4, 61, 72), (50, 7, 109, 118), (60, 8, 127, 142)],
)
def test_semantic_duration_examples(seconds, takes, minimum_words, maximum_words):
    contract = build_semantic_duration_contract(seconds)
    assert contract.minimum_take_count == takes
    assert (contract.minimum_words, contract.maximum_words) == (minimum_words, maximum_words)
    assert len(contract.contract_hash) == 64


def test_every_supported_integer_has_a_valid_contract():
    for seconds in range(8, 61):
        contract = build_semantic_duration_contract(seconds)
        assert contract.minimum_words <= contract.maximum_words
        assert contract.minimum_words > 18 * (contract.minimum_take_count - 1)


@pytest.mark.parametrize("seconds", [7, 61, 8.5, True, float("nan")])
def test_semantic_duration_rejects_invalid_values(seconds):
    with pytest.raises(ValueError):
        build_semantic_duration_contract(seconds)
```

- [ ] **Step 2: Run the duration tests and verify RED**

Run: `python3 -m pytest tests/test_semantic_duration_contract.py -q`

Expected: collection fails because `app.features.shot_production.duration` does not exist.

- [ ] **Step 3: Implement the immutable duration contract**

Implement a frozen dataclass with `requested_duration_seconds`, delivery bounds, take count, word bounds, semantic-block bounds, configured maximum, `as_dict()`, and a canonical sorted-JSON SHA-256. `build_semantic_duration_contract(value, maximum_seconds=None)` must read `SEMANTIC_UGC_MAX_DURATION_SECONDS` only when the explicit maximum is absent, default to 60, and reject booleans/non-integers/non-finite/out-of-range values.

```python
@dataclass(frozen=True)
class SemanticDurationContract:
    requested_duration_seconds: int
    delivery_min_seconds: float
    delivery_max_seconds: float
    minimum_take_count: int
    minimum_words: int
    maximum_words: int
    minimum_semantic_blocks: int
    maximum_semantic_blocks: int
    maximum_duration_seconds: int

    def as_dict(self) -> dict[str, int | float]:
        return {
            "requested_duration_seconds": self.requested_duration_seconds,
            "delivery_min_seconds": self.delivery_min_seconds,
            "delivery_max_seconds": self.delivery_max_seconds,
            "minimum_take_count": self.minimum_take_count,
            "minimum_words": self.minimum_words,
            "maximum_words": self.maximum_words,
            "minimum_semantic_blocks": self.minimum_semantic_blocks,
            "maximum_semantic_blocks": self.maximum_semantic_blocks,
            "maximum_duration_seconds": self.maximum_duration_seconds,
        }

    @property
    def contract_hash(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run the duration tests and verify GREEN**

Run: `python3 -m pytest tests/test_semantic_duration_contract.py -q`

Expected: all tests pass.

- [ ] **Step 5: Write failing script-rendering and validation tests**

```python
from app.features.topics.semantic_scripts import (
    build_semantic_script_prompt,
    generate_semantic_script,
    validate_semantic_script,
)


def test_semantic_prompt_renders_arbitrary_duration_without_tier_file():
    prompt = build_semantic_script_prompt(
        post_type="value",
        title="Barrierefreie Bahnreisen",
        cta="Speichere dir den Tipp.",
        facts=["Der Mobilitätsservice muss häufig vorab gebucht werden."],
        requested_duration_seconds=50,
    )
    assert "50" in prompt
    assert "109" in prompt and "118" in prompt
    assert "7" in prompt


def test_generated_script_must_fit_same_contract(fake_llm):
    fake_llm.text = " ".join(f"Wort{i}" for i in range(109)) + "."
    result = generate_semantic_script(
        post_type="value",
        title="Barrierefreie Bahnreisen",
        cta="Speichere dir den Tipp.",
        facts=["Fakt"],
        requested_duration_seconds=50,
        llm_client=fake_llm,
    )
    assert validate_semantic_script(result.script, requested_duration_seconds=50).minimum_take_count == 7
```

- [ ] **Step 6: Run script tests and verify RED**

Run: `python3 -m pytest tests/test_semantic_scripts.py -q`

Expected: import failure because the semantic script module and templates do not exist.

- [ ] **Step 7: Implement the three generic templates and script service**

`build_semantic_script_prompt` loads exactly one of the three family templates and injects the canonical contract. `generate_semantic_script` calls `generate_gemini_text`, strips fences/labels, validates the word envelope and `plan_editorial_beats`, and returns script plus provenance and contract hash. Provider failure uses a finite list of distinct fact-aware sentence templates; it never repeats a padding clause. `validate_semantic_script` requires the contract minimum take count and permits at most one extra take only when punctuation-aware planning cannot satisfy the minimum.

- [ ] **Step 8: Run focused tests and refactor while green**

Run: `python3 -m pytest tests/test_semantic_duration_contract.py tests/test_semantic_scripts.py tests/test_shot_production_planner.py -q`

Expected: all pass.

- [ ] **Step 9: Commit Task 1**

```bash
git add app/features/shot_production/duration.py app/features/topics/semantic_scripts.py app/features/topics/prompt_data/semantic_*.txt tests/test_semantic_duration_contract.py tests/test_semantic_scripts.py
git commit -m "feat: add semantic duration and script contracts"
```

### Task 2: Batch mode, database contract, and creation UI

**Files:**
- Create: `supabase/migrations/20260713_semantic_ugc_production.sql`
- Modify: `app/features/batches/schemas.py`
- Modify: `app/features/batches/handlers.py`
- Modify: `app/features/batches/queries.py`
- Modify: `templates/batches/list.html`
- Modify: `tests/test_batches_manual_mode.py`
- Modify: `tests/test_batches_queries.py`
- Create: `tests/test_semantic_batch_mode.py`

- [ ] **Step 1: Write failing schema and form tests**

Cover: `semantic_ugc` is accepted; `target_duration_seconds=50` is preserved; legacy tiers remain 8/16/32; semantic requests reject missing/7/61; semantic requests normalize `target_length_tier` to `None`; post counts remain required; form parsing accepts seconds; query payload contains `video_pipeline_route='semantic_ugc'`; duplicate copies seconds; list/detail responses expose seconds.

```python
def test_semantic_batch_uses_numeric_duration_only():
    payload = CreateBatchRequest(
        brand="AYRA",
        creation_mode="semantic_ugc",
        post_type_counts={"value": 1, "lifestyle": 0, "product": 0},
        target_duration_seconds=50,
    )
    assert payload.target_duration_seconds == 50
    assert payload.target_length_tier is None
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m pytest tests/test_semantic_batch_mode.py tests/test_batches_manual_mode.py tests/test_batches_queries.py -q`

Expected: failures for the unknown mode/field and missing persistence columns.

- [ ] **Step 3: Add the batch migration and normalized semantic tables**

The migration must:

- add `semantic_ugc` to creation-mode and pipeline-route checks;
- add nullable `batches.target_duration_seconds integer CHECK (target_duration_seconds IS NULL OR target_duration_seconds >= 8)`;
- permit `target_length_tier IS NULL` only when `creation_mode='semantic_ugc'`;
- create `semantic_video_runs`, `semantic_video_takes`, and `semantic_video_approvals` with UUID primary keys, foreign keys, timestamps, stage/check constraints, JSONB snapshots, hash fields, lease fields, and indexes;
- enforce one nonterminal run per post with a partial unique index;
- create an atomic `claim_semantic_video_run(worker_id, lease_seconds)` function using `FOR UPDATE SKIP LOCKED`.

- [ ] **Step 4: Implement Pydantic cross-field validation**

Add `semantic_ugc` to the literal, add `target_duration_seconds`, call the duration contract for the new mode, and preserve the legacy tier validator for every other mode. Add the field to batch response/detail models.

- [ ] **Step 5: Implement handlers and query persistence**

The create handler parses `target_duration_seconds` only for Semantic UGC, skips `normalize_target_length_tier` for that mode, persists the semantic route, and continues automated topic scheduling. Query projections, duplication, HTMX progress events, and response construction expose both duration authorities without synthesizing one from the other.

- [ ] **Step 6: Implement accessible conditional UI**

Add `Semantic UGC - Veo 3.1`; show a numeric input with min 8, max from template context/default 60 and preset buttons 8/16/32/50; hide and disable the legacy select while semantic is selected; explain that the shot plan and cost require approval. Preserve labels, keyboard focus, and current manual-mode behavior.

- [ ] **Step 7: Run tests and verify GREEN**

Run: `python3 -m pytest tests/test_semantic_batch_mode.py tests/test_batches_manual_mode.py tests/test_batches_queries.py tests/test_batches_status_progress.py -q`

Expected: all new tests pass and legacy assertions remain green.

- [ ] **Step 8: Commit Task 2**

```bash
git add supabase/migrations/20260713_semantic_ugc_production.sql app/features/batches templates/batches/list.html tests/test_semantic_batch_mode.py tests/test_batches_manual_mode.py tests/test_batches_queries.py
git commit -m "feat: add semantic ugc batch mode"
```

### Task 3: Duration-neutral topic selection and just-in-time scripts

**Files:**
- Modify: `app/features/topics/handlers.py`
- Modify: `app/features/topics/queries.py`
- Modify: `app/features/topics/schemas.py`
- Create: `tests/test_semantic_topic_generation.py`
- Modify: `tests/test_topic_pipeline.py`

- [ ] **Step 1: Write failing discovery tests**

Tests must prove that a 50-second semantic batch selects a topic family without querying a 50-second script-bank tier, calls `generate_semantic_script` with `requested_duration_seconds=50`, creates a post with `seed_data.target_duration_seconds=50`, stores the contract/provenance, leaves `target_length_tier` absent, and reaches seven planned beats. Add one test for each post family and a provider-failure fallback with no repeated sentence.

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m pytest tests/test_semantic_topic_generation.py -q`

Expected: current discovery falls back to tier 8 and either requests exact-tier coverage or validates with the legacy contract.

- [ ] **Step 3: Add a semantic discovery branch**

Detect `creation_mode='semantic_ugc'` once at the top of discovery. Use canonical 32-second family rows only as duration-neutral topic/research inputs; never expose that internal selector as the post duration. For each chosen topic, call the semantic script service with the batch's numeric target, replace the post rotation with the returned script, attach `target_duration_seconds`, `semantic_duration_contract`, `semantic_script_provenance`, and `script_review_status='pending'`, then call `create_post_for_batch` with `target_length_tier=None`.

Lifestyle and product generators may supply the topic/fact seed, but their legacy scripts must be adapted before persistence. Value discovery must not require exact 50-second audited script coverage.

- [ ] **Step 4: Expand only semantic text-capacity schemas**

Raise script/rotation capacity to 2,000 characters and remove 32-second spoken-duration caps where they would reject a semantic post. Keep legacy duration-tier literals and topic-table constraints unchanged.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python3 -m pytest tests/test_semantic_topic_generation.py tests/test_topic_pipeline.py tests/test_topic_prompt_templates.py -q`

Expected: semantic tests pass; the two documented ignored-`prompt1_batch.txt` baseline failures may remain until that local ignored asset is present.

- [ ] **Step 6: Commit Task 3**

```bash
git add app/features/topics tests/test_semantic_topic_generation.py tests/test_topic_pipeline.py
git commit -m "feat: generate semantic scripts for arbitrary durations"
```

### Task 4: Persisted plan, immutable approvals, and API

**Files:**
- Create: `app/features/semantic_videos/__init__.py`
- Create: `app/features/semantic_videos/schemas.py`
- Create: `app/features/semantic_videos/queries.py`
- Create: `app/features/semantic_videos/service.py`
- Create: `app/features/semantic_videos/handlers.py`
- Modify: `app/main.py`
- Create: `tests/test_semantic_video_plan.py`
- Create: `tests/test_semantic_video_handlers.py`

- [ ] **Step 1: Write failing pure planning tests**

Use an approved 50-second script and PNG bytes. Assert seven beats/takes, deterministic crops, canonical plan hash, exact billable seconds, quota units equal seven, estimated cost computed from provider seconds, and no provider call. Assert changed script/master/duration invalidates the hash. Assert Magnific and LoRA collaborators are never imported or called.

- [ ] **Step 2: Run plan tests and verify RED**

Run: `python3 -m pytest tests/test_semantic_video_plan.py -q`

Expected: import failure because the production feature slice does not exist.

- [ ] **Step 3: Implement schemas, pure plan compiler, and query payloads**

Define request/response models for candidate approval, plan creation, plan approval, retry approval, progress, and cancellation. `compile_semantic_video_plan` accepts explicit post/batch/reference snapshots and approved frame bytes, uses the duration contract, planner, shot deck, and prompt compiler, and returns JSON-safe run/take payloads plus SHA-256. Price defaults to the current full-model audio price of 0.40 USD/second but is configurable.

Queries implement create/get/update run, replace initial takes, append approval, list attempts, persist intent, persist accepted operation, persist QA/artifacts, acquire/release lease, and complete/fail projection. Writes validate affected row counts and use optimistic revisions.

- [ ] **Step 4: Write failing handler and approval tests**

Cover approved script/reference prerequisites, candidate-generation readiness, master approval, free-plan endpoint, progress response, initial approval hash match, stale hash rejection, retry approval only for failed indexes, and cancellation. Patch provider adapters and assert every endpoint except the explicitly invoked candidate-generation endpoint makes zero provider calls. Candidate generation must call only the shot-frame service and must never call Veo.

- [ ] **Step 5: Run handler tests and verify RED**

Run: `python3 -m pytest tests/test_semantic_video_handlers.py -q`

Expected: router/endpoints are absent.

- [ ] **Step 6: Implement handlers and register router**

Expose `/semantic-videos/posts/{post_id}/candidates`, `/master-approve`, `/plan`, `/progress`, `/approve`, `/retry-approve`, and `/cancel`. Candidate generation loads exactly two ordered actor references plus one actor-free location, invokes `generate_shot_frame_candidates`, writes all candidate bytes to object storage, and persists candidate metadata/hashes on the run. Use normal auth/error envelopes. Plan creation snapshots the approved script and reference hashes; approval appends an immutable row and moves the run to `generating`. No HTTP endpoint submits Veo synchronously.

- [ ] **Step 7: Run tests and verify GREEN**

Run: `python3 -m pytest tests/test_semantic_video_plan.py tests/test_semantic_video_handlers.py tests/test_shot_frames.py tests/test_shot_production_contract.py -q`

Expected: all pass.

- [ ] **Step 8: Commit Task 4**

```bash
git add app/features/semantic_videos app/main.py tests/test_semantic_video_plan.py tests/test_semantic_video_handlers.py
git commit -m "feat: persist semantic video plans and approvals"
```

### Task 5: Dedicated worker, paid safety, QA, and completion

**Files:**
- Create: `workers/semantic_video_worker.py`
- Modify: `app/features/semantic_videos/service.py`
- Modify: `app/features/shot_production/runner.py`
- Modify: `app/features/shot_production/voice_qa.py`
- Modify: `app/features/shot_production/acoustic_qa.py`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.yaml`
- Modify: `docker-compose.production.yml`
- Modify: `docker-compose.hostinger-runtime.yaml`
- Create: `tests/test_semantic_video_worker.py`
- Create: `tests/test_semantic_video_paid_safety.py`
- Modify: `tests/test_shot_production_voice_qa.py`
- Modify: `tests/test_shot_production_acoustic_qa.py`

- [ ] **Step 1: Write failing one-take QA tests**

Assert one audio clip returns a persisted `passed=True`, `status='not_applicable'` voice report without calling Gemini. Assert zero seams returns the same acoustic result. Existing two-or-more cardinalities remain unchanged.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m pytest tests/test_shot_production_voice_qa.py tests/test_shot_production_acoustic_qa.py -q`

Expected: one-take inputs currently raise validation errors.

- [ ] **Step 3: Implement the cardinality-safe N=1 behavior and verify GREEN**

Run the same command; expected all pass.

- [ ] **Step 4: Write failing worker and paid-safety tests**

Cover: missing approval, stale hash, exhausted budget, reservation failure, accepted/unknown existing operation, and submission cap all make zero calls; intent is persisted before the provider call; accepted operation is persisted immediately after; ambiguous exceptions become `submission_unknown`; resume polls instead of resubmitting; 50-second runs process seven fake requests in bounded waves; QA failure moves to retry approval; no automatic retry; final captioned output updates post directly and does not enqueue caption worker.

```python
def test_worker_never_exceeds_approved_submission_count(fake_repo, fake_vertex):
    run = semantic_run(max_submissions=1, approved_cost_usd=3.20)
    worker = SemanticVideoWorker(repo=fake_repo, vertex=fake_vertex)
    worker.tick(run.id)
    worker.tick(run.id)
    assert fake_vertex.submit_count == 1
```

- [ ] **Step 5: Run worker tests and verify RED**

Run: `python3 -m pytest tests/test_semantic_video_worker.py tests/test_semantic_video_paid_safety.py -q`

Expected: worker module and stage orchestration are absent.

- [ ] **Step 6: Implement one-stage-per-tick worker**

The worker claims a run through the SQL lease, reloads current state, executes at most one stage/provider wave, persists evidence, and releases or renews the lease. Initial submissions consume exactly one reserved quota unit each. Price and quota are separate. Max in-flight defaults to two, but the run's approved submission/budget limits always win.

Reuse existing shot-production functions through injectable storage/repository boundaries. Object storage receives every artifact and checksum. Local files are temporary. Completion writes the final captioned URL/checksum to both semantic run and post, sets `caption_completed`, and invokes normal batch reconciliation without sending the post to caption worker.

- [ ] **Step 7: Verify GREEN and regression safety**

Run: `python3 -m pytest tests/test_semantic_video_worker.py tests/test_semantic_video_paid_safety.py tests/test_shot_production_runner.py tests/test_video_quota_guard.py tests/test_video_poller_caption_handoff.py -q`

Expected: all pass.

- [ ] **Step 8: Commit Task 5**

```bash
git add workers/semantic_video_worker.py app/features/semantic_videos app/features/shot_production tests/test_semantic_video_worker.py tests/test_semantic_video_paid_safety.py tests/test_shot_production_voice_qa.py tests/test_shot_production_acoustic_qa.py
git commit -m "feat: run resumable semantic video production"
```

### Task 6: Batch-detail workflow and real browser verification

**Files:**
- Create: `templates/batches/detail/_semantic_video.html`
- Create: `static/js/batches/semantic_video.js`
- Modify: `templates/batches/detail.html`
- Modify: `app/features/batches/handlers.py`
- Modify: `tests/test_batches_status_progress.py`
- Create: `tests/test_semantic_video_ui.py`

- [ ] **Step 1: Write failing template/projection tests**

Assert Semantic UGC detail exposes requested/delivery duration, master state, exact take/provider-second/cost values, progress counts, current stage, retry indexes, and incremental cost. Assert legacy detail remains unchanged. Assert controls have labels and approval buttons are disabled when hashes are stale or the required prior approval is absent.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m pytest tests/test_semantic_video_ui.py tests/test_batches_status_progress.py -q`

Expected: semantic projection and partial do not exist.

- [ ] **Step 3: Implement the partial, JS controller, and projection**

Render the semantic partial only for `creation_mode='semantic_ugc'`. Provide candidate-generation and candidate-selection controls that show stored images before master approval. Poll the progress endpoint, update `generated/total` and `verified/total`, show master/plan approvals, and require an explicit confirmation dialog containing the exact dollar amount. Retry approval lists only failed take indexes and incremental price. Use semantic buttons/forms with keyboard focus and status live regions.

- [ ] **Step 4: Run focused UI tests and verify GREEN**

Run: `python3 -m pytest tests/test_semantic_video_ui.py tests/test_batches_status_progress.py -q`

Expected: all pass.

- [ ] **Step 5: Start the real app and verify in browser**

Run: `DISABLE_BACKGROUND_SCHEDULERS=1 DISABLE_STARTUP_RECOVERY_CHECKS=1 uvicorn app.main:app --host 127.0.0.1 --port 8000`

Use the in-app browser to create/open a Semantic UGC batch, select 50 seconds, verify the numeric input and plan/cost states, exercise keyboard focus, resize to mobile width, and confirm no console or overflow errors. Do not trigger image or video generation during browser QA.

- [ ] **Step 6: Commit Task 6**

```bash
git add templates/batches static/js/batches app/features/batches/handlers.py tests/test_semantic_video_ui.py tests/test_batches_status_progress.py
git commit -m "feat: add semantic video approval workflow UI"
```

### Task 7: One-request live Veo proof and final verification

**Files:**
- Create: `scripts/run_semantic_ugc_live_smoke.py`
- Create: `tests/test_semantic_live_budget_guard.py`
- Create runtime evidence under ignored `output/semantic-ugc-live-proof/<run-id>/`

- [ ] **Step 1: Write failing live-guard tests**

Assert the harness rejects more than one planned take, more than one output, missing approved hash, estimated cost above 17.70, any retry flag, and any image-generation collaborator. Assert one eight-second full-model audio request estimates USD 3.20 at USD 0.40/second and passes. Assert a second call attempt raises before reaching Vertex.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m pytest tests/test_semantic_live_budget_guard.py -q`

Expected: smoke harness/guard is absent.

- [ ] **Step 3: Implement the live guard and dry-run evidence**

The CLI requires an existing approved frame, expected SHA-256, existing approved script input, output directory, `--max-budget-usd 17.70`, `--max-submissions 1`, and explicit `--confirm-paid-plan`. It initializes a one-take manifest, writes the price/approval contract, and refuses every retry/resume state that could submit a second operation. Dry-run prints the exact request hash and estimated cost without instantiating paid image, transcription, QA, or upload providers.

- [ ] **Step 4: Run guard tests and full dry run**

Run: `python3 -m pytest tests/test_semantic_live_budget_guard.py -q`

Expected: all pass.

Run the CLI without `--confirm-paid-plan`; expected: it stops before Vertex and records one pending request with estimated cost at or below USD 3.20.

- [ ] **Step 5: Run focused and broad automated verification before spending**

Run all new semantic tests plus the shot-production, batch, topic, video routing, quota, caption, and status suites. Then run the full repository suite and classify any failures against the recorded baseline. Do not make a provider call until new tests are green and the only remaining failures are confirmed pre-existing.

- [ ] **Step 6: Submit exactly one live Veo request**

Use the existing approved frame and an approved 14-18 word eight-second script. Run once with `--confirm-paid-plan`. Record the returned operation ID immediately. Poll that operation without resubmitting. On rejection, ambiguity, timeout, provider failure, or QA failure, stop; do not retry.

- [ ] **Step 7: Validate and preserve the output**

Verify MP4 container, 9:16 dimensions, audio stream, actual duration, identity frames, checksums, and the final request-count ledger. Use only local FFmpeg/FFprobe and human inspection. Create deterministic local captions from the known approved one-take script without Deepgram, Gemini, image generation, upload, or any other paid provider. Preserve the manifest and evidence proving exactly one submission and estimated spend below USD 17.70.

- [ ] **Step 8: Commit Task 7 implementation (never generated media or secrets)**

```bash
git add scripts/run_semantic_ugc_live_smoke.py tests/test_semantic_live_budget_guard.py
git commit -m "test: add one-request semantic veo live proof"
```

### Task 8: Final review and integration

**Files:**
- Review all files changed by Tasks 1-7.

- [ ] **Step 1: Run plan/spec coverage review**

Confirm every acceptance criterion in `docs/superpowers/specs/2026-07-13-semantic-ugc-production-mode-design.md` has code and test evidence. Confirm no semantic path imports Magnific or requires LoRA.

- [ ] **Step 2: Run formatting, diff, migration, and complete test checks**

Run `git diff --check`, focused tests, full tests, migration lint/check commands available in the repo, and a final browser smoke. Confirm no credentials, generated media, operation tokens, or local absolute paths are tracked.

- [ ] **Step 3: Request spec-compliance and code-quality reviews**

Fix every material finding and rerun affected tests. Obtain final approval from an independent reviewer.

- [ ] **Step 4: Merge the reviewed branch into `main` only after all gates pass**

Use the finishing-development-branch workflow. Re-run the focused suite on `main`, confirm the new mode is present, and preserve the live proof evidence without making another provider request.
