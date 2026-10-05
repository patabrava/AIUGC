# Active work

## Concurrent unfinished task: direct ModelArk implementation

Intent/acceptance: preserve the existing direct Seedance evaluation implementation and its pending external authorization checkpoints. Perspective: preserve original provider and identity contracts; inspect original evidence before any next paid action. Supported correction: publication of code does not accept agreements, authorize paid requests, or apply hosted migrations.

# Direct ModelArk implementation block

BRIDGECODE_ROUTE: direct Seedance API refactor → [GENERAL, RESEARCH, LIRA, EYE] | MODE: Mixed | WHY: Verify the direct API and portrait authorization before implementing durable submission and spend contracts.

Goal: direct BytePlus Seedance 2.5 evaluation, 8 seconds, 480p portrait, canonical actor/source unchanged. Keep production Veo delivery untouched; user-required Seedance never falls back to another video model.

{files, LOC/file, deps}: ModelArk REST adapter ~210, shared safe downloader ~220, provider profiles ~60, existing evaluation service ~1500, config ~25 new, migration ~350, focused tests ~210; existing httpx/Pydantic/PostgreSQL only. Legacy evaluation table and RPC names preserved for rolling compatibility; new `/seedance-evaluations` routes share the same durable boundary.

Contracts: host/model/quota tuple fenced in SQL; separate integer microdollar budget; snapshot token price before admission; one paid POST without retry; ambiguous acknowledgement stays blocked; polling fenced by host/environment/scope/lease; returned token usage settles actual spend; overspend freezes future ModelArk admission; downloader checks HTTPS/host/redirect/byte caps; ffprobe duration/aspect/audio checks; no posts/takes/publishing mutation. Original actor refs and source bytes are reverified. Previously moderated exact source/prompt remains blocked across hosts. Trusted asset URI requires matching original-image SHA-256.

Validation: focused adapter/service/HTTP/canary regressions plus real isolated PostgreSQL migration, legacy lifecycle, host-isolated claims, model mismatch, actual cost, zero-cost failures, overspend freeze and permissions. Nonpaid CLI probe has confirmed missing ModelArk key locally. Docker test container isolated with network none; unrelated containers untouched.

External checkpoint: BytePlus API-key UI is behind first-use legally binding agreement. Explicit acceptance question pending; no agreement accepted, key created, purchase made, hosted migration applied or paid ModelArk task submitted. The new exact hosted migration also has a separate pending approval. `.env` contains direct provider settings and a blank MODELARK_API_KEY with paid evaluation disabled. After account access: inspect model activation and portrait library authorization, obtain approved key without exposing it, apply only approved migration, preview AYRA source and submit only if upstream source authorization is established. Real-person asset verification must be completed by the depicted person; do not substitute a generated sheet for trusted authorization.
