# Security Best Practices Report (Focused on Recent Commits)

Date: 2026-03-22
Scope: Secret leakage + high-impact security regressions in the last commit set (`04e1fb7`, `4a7de29`, `f4d1118`, `eab610c`, `9b2b815`, `674c44c`).
Stack observed: Python/FastAPI backend.

## Executive summary
- No private keys / API keys were committed in the last commits (or in `HEAD`) based on pattern scans for common key formats.
- One **High** risk remains in app code: Gemini API key is passed as a **query parameter** (`?key=...`) and can be leaked by HTTP client request logging / proxies.
- One **Medium** risk in testscripts: helper scripts alias `SUPABASE_KEY` to `SUPABASE_SERVICE_ROLE_KEY`, which increases the chance of accidentally using a service-role key in the wrong place.

---

## Findings

### [S-1] Gemini API key can leak via URL logging
- Severity: High
- Rule: "MUST NOT request, output, log, or commit secrets" (FastAPI spec §0)
- Location:
  - `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/llm_client.py:60`
  - `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/llm_client.py:69`
- Evidence:
  - `_gemini_params()` returns `{"key": self.gemini_api_key}` and every Gemini call uses `params=self._gemini_params()`.
- Impact:
  - Any HTTP request logging that prints the full URL (common with httpx debug logging, proxies, APM, reverse proxies) can record the API key in plaintext.
- Fix (recommended):
  - Prefer `x-goog-api-key` header instead of query param for Gemini calls (similar to `app/adapters/veo_client.py` which already uses `x-goog-api-key`).
  - As defense-in-depth, add log redaction for `key` query params in your HTTP logging layer.
- Notes:
  - This is not a “commit leaked a key” finding; it’s an operational leak vector that can expose keys during normal runs.

### [S-2] Testscripts alias service-role key into `SUPABASE_KEY`
- Severity: Medium
- Location:
  - `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/agents/testscripts/testscript_deep_research_trace.py:70`
- Evidence:
  - If `SUPABASE_KEY` is absent, script sets `SUPABASE_KEY = SUPABASE_SERVICE_ROLE_KEY`.
- Impact:
  - Increases risk of confusing anon vs service-role usage; if anyone reuses this pattern outside a trusted backend context, it can accidentally widen privileges.
- Fix (recommended):
  - Keep `SUPABASE_KEY` and `SUPABASE_SERVICE_ROLE_KEY` distinct.
  - For testscripts, require `SUPABASE_SERVICE_ROLE_KEY` explicitly and do not backfill `SUPABASE_KEY`.

---

## Secret leakage check (recent commits)
Methods used:
- `git grep` scans on `HEAD` for common key patterns: Google API keys (`AIza...`), OpenAI keys (`sk-...`), JWTs, private key PEM headers, AWS access keys, Supabase secret patterns.
- `git log -p -n 6` scanned for the same patterns.

Result:
- No concrete secrets found committed.
- Matches found were **documentation placeholders** (`.env.example`, `README.md`, `setup.sh`) and **environment variable names**, not real key material.

---

## Quick recommendations
1. Implement [S-1] (header-based Gemini auth + log redaction) before broader rollout.
2. Tweak testscripts per [S-2] to avoid accidental key-class confusion.

