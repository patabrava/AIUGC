# Direct BytePlus ModelArk Seedance 2.5

BRIDGECODE_ROUTE: direct API refactor → [GENERAL, RESEARCH, LIRA, EYE] | MODE: Mixed | WHY: Verify the current provider contract, preserve immutable identity, and prove durable submission and spend accounting.

## Implemented

Direct REST adapter uses `https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks`, model `dreamina-seedance-2-5-260628`, Bearer server credential, eight seconds, 480p/720p portrait with generated audio. Existing evaluation-only scope remains: no production video, captions or publishing mutations. `/seedance-evaluations/posts/{post_id}/preview` and POST `/seedance-evaluations/posts/{post_id}` expose the selected host; old Runway URLs remain compatible. Submit accepts `confirm_estimated_units` (old `confirm_estimated_credits` remains accepted).

Configuration defaults to ModelArk. `.env`, `.env.example`, `.env.production.example` include MODELARK settings; local credential remains blank and MODELARK_EVALUATION_ENABLED remains false until access is verified. Existing credentials are preserved. Separate operator allowlist, host allowlist and budget apply. Budget units are integer USD microdollars: default per run $2, daily $10. At current $10.70/M output tokens, 8s 480p 9:16 estimates $0.822402; admission reserves $0.945763 with alignment margin. Actual returned completion tokens settle spend under the persisted rate. Excess actual cost freezes new ModelArk admissions.

Migration `supabase/migrations/20261005000000_modelark_seedance_evaluations.sql` retains the legacy evaluation table and RPC names. It fences host/model/quota tuples, separates poll claims, and settles actual costs transactionally. RPC-only writes and prior submission fences remain. Historical Runway tasks continue polling through their own adapter. Ambiguous paid POSTs never retry. ModelArk DELETE is not used as proof of running-task cancellation because it may delete task records.

Original actor and shot checksums remain authoritative. Trusted `asset://` references require MODELARK_REFERENCE_ASSET_SHA256 equal to the exact verified submitted image. Cross-host moderation history blocks rejected exact source/prompt fingerprints. An API-host change does not grant portrait authorization.

## Validation

Run focused tests with disposable PostgreSQL:

```sh
SEMANTIC_UGC_POSTGRES_CONTAINER=aiugc-modelark-validation-pg .venv/bin/pytest -q tests/test_modelark_evaluation_migration_postgres.py tests/test_modelark_client.py tests/test_runway_evaluation_migration_postgres.py tests/test_runway_client.py tests/test_runway_evaluations.py tests/test_runway_sheet_canary.py tests/test_runway_new_character_canary.py
```

Result: **177 passed**, four dependency deprecation warnings. The tests use real PostgreSQL 16 to apply both migrations transactionally and prove legacy lifecycle compatibility, host-isolated leases, RPC permissions, tuple rejection before quota reservation, final usage settlement, zero-cost failure refund and excess-cost freeze. Adapter/service tests cover native JSON, credential containment, no paid retry, moderation, actor checks, acknowledgement fencing, output redirect/byte validation, real ffprobe checks and HTTP exact-cost confirmation. No new dependency.

Non-paid credential probe:

```sh
.venv/bin/python -m scripts.run_modelark_evaluation probe
```

Current result: configuration blocked because no ModelArk key exists locally. No live ModelArk request or generated video is claimed.

After approved account setup, free preview then explicit one-shot submission:

```sh
.venv/bin/python -m scripts.run_modelark_evaluation preview --post-id POST_UUID --operator-email OPERATOR_EMAIL --resolution 480p
.venv/bin/python -m scripts.run_modelark_evaluation submit --post-id POST_UUID --operator-email OPERATOR_EMAIL --resolution 480p --confirm-units PREVIEW_RESERVED_UNITS
.venv/bin/python -m scripts.run_modelark_evaluation poll
```

Commands must use a real approved Semantic post. A previously rejected exact source is blocked until provider review; do not alter it to evade rejection. Poll the existing evaluation rather than resubmitting after an uncertain response.

## External checkpoint

Computer Use reached the BytePlus API-key page, which displays first-use Terms of Service, Customer Agreement and Data Processing Addendum consent. Explicit user approval to accept is pending. Screenshot: `output/modelark-integration-2026-10-05/byteplus-agreement.jpg`. No agreement accepted, credential created, purchase made, migration applied hosted, or paid task sent. Approval to apply only the new exact migration is separately pending. Preserve the browser handoff tab.

After agreement approval: inspect existing key or ask action-time permission to create a scoped key; save it privately in `.env`; perform nonpaid auth probe; inspect model activation/account balance; inspect AYRA portrait library rights; apply only the approved migration and verify RPC ACL/schema reload; then run a supported, explicitly cost-confirmed AYRA canary. Required real-person verification is performed by the depicted person. Purchases require exact merchant/purpose/spending authorization.

Official sources (checked 2026-10-05):

- [Seedance 2.5 contract](https://docs.byteplus.com/en/docs/modelark/seedance-2-5)
- [Pricing and returned token formula](https://docs.byteplus.com/en/docs/modelark/model-pricing)
- [Model activation requirements](https://docs.byteplus.com/en/docs/modelark/seedance-model-activation-usage-and-refund)
- [Private real-human asset library (invited users)](https://docs.byteplus.com/en/docs/modelark/guide-preview?redirect=1)
- [Advanced Creation Rights](https://docs.byteplus.com/en/docs/modelark/seedance-2-0-purchase-guide): documented free Entry tier supports up to 50 assets/groups, subject to enterprise verification and agreement requirements; paid enterprise tiers are optional capacity increases. Do not assume the account has these rights until the console confirms them.
