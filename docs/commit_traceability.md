# Commit Traceability Log

Date: 2026-03-21

This log maps recent commits to functional impact so operators can audit behavior changes quickly.

## Current Session Commits

### `335ed81` feat(publish): add PostMetaNowRequest schema with Meta-only validation
- Scope:
  - updated [app/features/publish/schemas.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/publish/schemas.py)
- Impact:
  - adds strict request contract for immediate Meta publishing
  - enforces network allowlist (`facebook`, `instagram`) and blocks unsupported targets
  - prevents duplicate network selections at schema boundary

### `e6898fd` docs(agents): update negentropized instructions to topics hub contract
- Scope:
  - updated [agents/negentropized_instructions.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/agents/negentropized_instructions.md)
- Impact:
  - refreshes generated implementation intent from Meta-login-focused contract to topics-hub contract
  - preserves a distinct commit boundary for generated planning artifact changes

### `782009e` docs(architecture): add current FLOW-FORGE runtime architecture
- Scope:
  - added [architecture.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/architecture.md)
- Impact:
  - documents current runtime topology, batch state machine, topic/script flow, video pipeline, and publish boundaries

## Recent Existing Hardening Commits (already in history)

### `ec079c3` Harden host validation for prod
- Files: `app/main.py`, `app/core/config.py`, `README.md`
- Why:
  - ensure trusted host behavior is explicit and production-safe

### `b737ff1` Remove seeded secrets from setup script
- Why:
  - prevent unsafe secret defaults in setup path

### `1490158` Harden config, errors, and QA gating
- Why:
  - improve error boundary behavior and QA transition robustness

### `4879f17` fix: gate tiktok direct posts in sandbox
- Why:
  - block invalid direct-post path in sandbox mode

### `2ef51b4` fix: normalize tiktok oauth rpc payloads
- Why:
  - support both dict and list-shaped Supabase RPC returns

### `5696c18` fix: serve tiktok sandbox verification file
- Why:
  - satisfy TikTok sandbox verification boundary

### `8ad6715` fix: split tiktok job and result statuses
- Why:
  - clarify publish-state semantics and avoid false terminal interpretations
