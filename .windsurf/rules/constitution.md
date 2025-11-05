---
trigger: always_on
---


**Binding Rules for LLM Assistants on FLOW-FORGE UGC System**  
**Date:** 2025-11-05  
**Authority:** Project Canon v1.0 + LLM-Friendly Engineering + LLM-Friendly Testing/Debugging

---

## I. CANON SUPREMACY

### DO: Treat Canon as Sole Source of Truth
- **Read Canon first.** Before any code change, verify alignment with `CANON.md` and `IMPLEMENTATION_GUIDE.md`.
- **Canon wins conflicts.** If implementation contradicts canon, canon is correct by definition.
- **Version-lock decisions.** Reference canon version number in commit messages and PRs.
- **Propagate canon changes.** When canon updates, cascade changes to code, tests, and docs immediately.

### DON'T: Invent Outside Canon
- **No undocumented surfaces.** Every endpoint, state, or UI element must exist in canon first.
- **No creative interpretation.** If canon says "≤32 words," do not implement "≤35 words for flexibility."
- **No silent extensions.** Adding "helpful" features without canon approval violates determinism.

---

## II. VALIDATED BOUNDARIES

### DO: Enforce Contracts Everywhere
- **Schema-validate at edges.** Every API input/output, form submission, and agent response must pass Pydantic validation.
- **Standard error envelopes.** Use `{ok: false, code, message, details}` for all failures; no raw exceptions to users.
- **Explicit state guards.** State transitions require guard checks; reject invalid transitions with `state_transition_error`.
- **Boundary logging.** Log all inputs/outputs at feature boundaries with correlation IDs.

### DON'T: Trust Implicit Contracts
- **No unvalidated data flows.** Never pass user input or LLM output directly to DB/API without schema check.
- **No silent coercion.** If validation fails, fail loudly with actionable error, not silent fallback.
- **No magic parsing.** Avoid regex/string hacks where structured schemas exist.

---

## III. DETERMINISTIC EXECUTION

### DO: Make Everything Reproducible
- **Pin all versions.** `requirements.txt` must specify exact versions; no `>=` or `latest`.
- **Seed randomness.** Any random/sampling operation must accept explicit seed parameter.
- **Idempotent operations.** Support `Idempotency-Key` headers on all POST endpoints; cache responses 24h.
- **Explicit time handling.** Use UTC internally; convert to `Europe/Berlin` only at display/scheduling boundaries.
- **One-command setup.** `pip install -r requirements.txt && python app/main.py` must work cold.

### DON'T: Introduce Non-Determinism
- **No implicit global state.** Avoid module-level mutable singletons; use dependency injection or explicit factories.
- **No time.now() without context.** Always pass time as parameter or use injectable clock.
- **No hidden side effects.** Functions must not mutate external state unless explicitly named (e.g., `save_`, `update_`).

---

## IV. DEFINITION OF DONE

### DO: Meet All Criteria Before Claiming Done
1. **Conforms to canon.** Feature matches spec in `CANON.md` exactly.
2. **Boundaries validated.** All inputs/outputs have Pydantic schemas and runtime checks.
3. **Testscript passes.** Phase-specific whole-app testscript executes green.
4. **Prior phases re-verified.** All earlier testscripts still pass (regression guard).
5. **Observable.** Structured logs with correlation IDs at feature boundaries.
6. **Documented.** README updated if new commands/env vars introduced.

### DON'T: Ship Incomplete Work
- **No "works on my machine."** If testscript fails for Human-as-EYE, it's not done.
- **No deferred validation.** "We'll add schemas later" is not acceptable.
- **No skipped regression.** Must re-run prior phases; cannot assume they still work.

---

## V. LOCALITY & VERTICAL SLICES

### DO: Co-Locate Feature Logic
- **Feature folders.** Group handler + schema + queries + templates in `/app/features/{feature}/`.
- **Keep files 100-300 LOC.** Split by responsibility (e.g., `schemas.py`, `handlers.py`, `queries.py`) when exceeded.
- **Explicit dependencies.** Import only from `core/`, `adapters/`, or sibling features; no circular deps.
- **Single responsibility per file.** `batches/schemas.py` contains only batch-related Pydantic models.

### DON'T: Scatter or Duplicate
- **No parallel patterns.** One router (FastAPI), one DB client (Supabase SDK), one style system (Tailwind).
- **No utils soup.** Avoid `utils.py` with unrelated helpers; co-locate or promote to `core/`.
- **No hidden layers.** Do not introduce middleware, decorators, or metaclasses that obscure control flow.

---

## VI. VANILLA-FIRST IMPLEMENTATION

### DO: Prefer Standard Library and Primitives
- **Vanilla by default.** Use Python stdlib, FastAPI built-ins, and Supabase SDK before adding libraries.
- **Docs-heavy choices.** When adding a library, verify ≥3 canonical docs sources and high predictability.
- **Adapterize specialists.** Wrap LLM SDKs (OpenAI, Anthropic) and video APIs (Veo, Sora) in thin `/adapters/` modules.
- **One tool per concern.** Single job scheduler (APScheduler), single HTTP client (httpx), single template engine (Jinja2).

### DON'T: Over-Engineer or Over-Abstract
- **No premature frameworks.** Do not introduce ORMs, DI containers, or event buses without canon approval.
- **No low-docs libraries.** Avoid experimental or niche packages with sparse documentation.
- **No abstraction before duplication.** Wait for "rule of three" before extracting shared code.

---

## VII. STATE MACHINE DISCIPLINE

### DO: Respect State Transitions
- **Explicit guards.** Check current state and preconditions before allowing transition.
- **Atomic updates.** State changes must be transactional; rollback on failure.
- **Audit trail.** Log every state transition with timestamp, actor, and reason.
- **Enforce at DB.** Use PostgreSQL `CHECK` constraints to prevent invalid states.

### DON'T: Bypass or Shortcut States
- **No state skipping.** Cannot jump `S1_SETUP → S4_SCRIPTED` without passing `S2_SEEDED`.
- **No backdoor mutations.** Do not update post fields that imply state change without formal transition.
- **No optimistic assumptions.** Always verify current state before operation; never assume.

---

## VIII. WHOLE-APP TESTSCRIPTS

### DO: Test End-to-End in Real Environment
- **Phase-gated delivery.** Each feature phase ships with a runnable testscript that exercises the live app.
- **Explicit pass/fail.** Testscripts define observable checkpoints and success criteria.
- **Re-verify prior phases.** Every new testscript re-runs all earlier scripts to catch regressions.
- **Human-as-EYE protocol.** Provide clear instructions for human operator: what to run, observe, and capture.
- **Artifact collection.** Specify logs, screenshots, API responses to gather at each checkpoint.

### DON'T: Rely on Isolated Unit Tests Alone
- **No detached mocks.** Do not test against fake DB/API stubs unless adapterized and contract-tested.
- **No silent passes.** Tests must fail loudly with structured evidence when expectations unmet.
- **No skipped regression.** Cannot proceed to next phase if prior testscripts fail.

---

## IX. OBSERVABLE IMPLEMENTATION

### DO: Instrument for Debuggability
- **Structured logging.** Use `structlog` with JSON output; include `correlation_id`, `feature`, `state`, `actor`.
- **Correlation IDs.** Generate unique ID per request; propagate through all logs and external calls.
- **Explicit errors.** Raise custom exceptions with error codes; catch and convert to standard envelopes at boundaries.
- **Health endpoints.** Expose `/health` with DB connectivity, job scheduler status, and version info.
- **Debug mode.** Support `DEBUG=1` env var for verbose logging; document in README.

### DON'T: Hide Behavior or Swallow Errors
- **No silent catches.** Avoid bare `except:` or `except Exception: pass`; always log and re-raise or convert.
- **No opaque magic.** Do not use reflection, metaclasses, or dynamic imports that obscure control flow.
- **No missing context.** Errors must include actionable details (e.g., which field failed validation, expected vs. actual).

---

## X. HYPOTHESIS-DRIVEN DEBUGGING

### DO: Isolate Root Cause Systematically
- **Reproduce first.** Create minimal reproducer that triggers failure in real environment.
- **Instrument before editing.** Add logs/breakpoints at suspected boundary before changing code.
- **One variable at a time.** Change single parameter, re-run, observe; iterate until root cause found.
- **Boundary verification.** Check inputs/outputs at feature boundaries; compare expected vs. actual schemas.
- **Regression test.** Once fixed, add testscript check that would have caught the bug.

### DON'T: Guess or Shotgun Debug
- **No speculative fixes.** Do not change code without hypothesis and observation plan.
- **No multi-variable changes.** Changing multiple things simultaneously obscures causation.
- **No silent workarounds.** If root cause unclear, escalate with structured evidence request, not band-aid.

---

## XI. ADAPTATION FROM PLAN

### DO: Derive Decisions from Canon
- **Language/Framework.** Python 3.11+, FastAPI, Supabase per canon § 2.1-2.3.
- **Repo Layout.** Vertical slices in `/app/features/`, adapters in `/app/adapters/`, per canon § 9.
- **Error Model.** Standard envelopes per canon § 5.1-5.3.
- **Security.** Supabase Auth for authentication; env vars for secrets; no hardcoded keys.
- **Observability.** `structlog` with correlation IDs per canon § 2.5.
- **Performance.** Video polling via Railway worker; async where I/O-bound.
- **Accessibility.** WCAG AA, keyboard nav, ARIA labels, `prefers-reduced-motion` support.
- **Deployment.** Vercel (API) + Railway (worker) per Implementation Guide.

### DON'T: Assume or Invent
- **No undocumented choices.** If canon is silent, pick simplest widely-documented option and note assumption in commit.
- **No framework switching.** Do not replace FastAPI with Flask or Django without canon amendment.
- **No schema drift.** Do not alter DB schema without migration and canon update.

---

## XII. AGENT PROMPT DISCIPLINE

### DO: Validate LLM Outputs
- **Schema-first.** Define Pydantic model for agent output before calling LLM.
- **Retry with feedback.** If LLM output fails validation, retry with error details (max 3 attempts).
- **Explicit constraints.** Include all canon rules in system prompt (e.g., "≤32 words, ends '(stiller Halt)', no hyphens").
- **Structured output.** Use JSON mode or function calling; avoid unstructured text parsing.
- **Audit trail.** Log prompt, response, and validation result with correlation ID.

### DON'T: Trust Raw LLM Output
- **No direct passthrough.** Never send LLM response to DB/API without validation.
- **No silent fallback.** If validation fails after retries, surface error to user; do not substitute default.
- **No hallucination tolerance.** Strict Extractor must only extract factual seeds; reject creative additions.

---

## XIII. IDEMPOTENCY & RECOVERY

### DO: Support Safe Retries
- **Idempotency keys.** Accept `Idempotency-Key` header on all POST endpoints; store in Supabase 24h.
- **Cached responses.** Return cached result if duplicate key detected; do not re-execute.
- **Graceful degradation.** If external service fails, return `third_party_fail` with retry guidance.
- **Job retries.** APScheduler jobs must be idempotent; check state before re-executing.

### DON'T: Create Duplicate Side Effects
- **No double-posting.** Video generation, social publishing must check for existing operation before starting.
- **No lost updates.** Use optimistic locking or transactions to prevent concurrent modification conflicts.

---

## XIV. SECURITY & COMPLIANCE

### DO: Follow Best Practices
- **Secrets in env.** Use `.env` files (gitignored) or Vercel/Railway env vars; never hardcode.
- **Input sanitization.** Validate and escape all user inputs; use Pydantic for type safety.
- **Auth checks.** Verify Supabase Auth token on protected endpoints; return `auth_fail` if invalid.
- **Rate limiting.** Implement per-user 