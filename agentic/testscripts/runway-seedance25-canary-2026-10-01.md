# Runway Seedance 2.5 validation — 2026-10-01

BRIDGECODE_ROUTE: validation, env, migration, and external integration from an existing implementation → [GENERAL, RESEARCH, EYE] | MODE: Mixed | WHY: Verify the existing evaluation slice through offline tests, real PostgreSQL, hosted permissions, and unpaid admission gates before live spend.

## Initial outcome — 2026-10-01

Local wiring, credential verification, hosted migration, and one explicitly approved 480p eight-second live submission are complete. Runway accepted the request with a 160-credit estimate, then rejected it at third-party content moderation (`INPUT_PREPROCESSING.SAFETY.THIRD_PARTY`). Provider-reported actual cost is 0 credits; the organization balance remains 500. No video exists, so successful media materialization and quality comparison remain unvalidated. Production post video, run, and take hashes are unchanged. No second submission, production-secret change, or commit was performed. The local evaluation flag remains false; the canary enabled it only in its process.

## Changes

Added all 20 requested Runway settings to `.env.example`, `.env.production.example`, and local `.env`, preserving existing content. The user-provided API key is saved only in ignored local `.env` (fingerprint `838f2c3eef53`). Local operator is `caposk817@gmail.com`, allowed resolutions are `480p,720p,1080p`, and the exact docs-known output host is `dnznrvs05pmza.cloudfront.net`. The persistent flag remains false. Production credentials were not changed.

Added the Runway PostgreSQL lifecycle test, migration-version assertion, RPC count check, and table-privilege checks to `scripts/test_semantic_video_migrations.sh`. Existing Manual Semantic test wiring was preserved. Added compact harness prevention rules for Docker auto-restarted consumers and isolated migration-history fetches.

## Validation

| Task | Status | Evidence |
| --- | --- | --- |
| A: env keys | Passed | Configuration block added to all three env files; user-provided Runway key saved in ignored local env and verified via free organization API. Davinci UI had no API-key controls. |
| B: focused offline tests | Passed | Initial run: 129 passed, 1 PostgreSQL skip. With `SEMANTIC_UGC_POSTGRES_CONTAINER=aiugc-runway-pg-20261001`: 130 passed, no skips. Poller isolation test ran and passed. |
| B: adjacent regression suites | Existing failures | 90 passed, 2 failed. Both failures are retired character-consistency expectations at `tests/test_video_quota_guard.py:669` and `:806` (`submitted_count` expected 1, observed 0). |
| B: full suite | Existing failures | `pytest -q tests` cannot collect `tests/test_hardening_regressions.py:27`: missing `_active_posts_ready_for_publish` import. Rerun excluding only that file: 2687 passed, 36 failed, 180 skipped. No failed assertion exercises the Runway slice; config failures concern existing Vertex/env expectations. |
| C: isolated PostgreSQL | Passed | `SEMANTIC_UGC_POSTGRES_CONTAINER=aiugc-runway-pg-20261001 .venv/bin/python -m pytest -q tests/test_runway_evaluation_migration_postgres.py`: 2 passed. |
| C: full migration chain | Passed | `SEMANTIC_UGC_PYTHON_BIN=.venv/bin/python scripts/test_semantic_video_migrations.sh`: 23 PostgreSQL tests passed; all migrations applied on the fresh database; final version/RPC/privilege assertions passed. |
| D: hosted migration | Passed | User explicitly approved. Applied only the Runway SQL in a transaction through the Supabase Management query API, recording version 20261001000000. RPCs and privileges verified; PostgREST schema reloaded; Data API table SELECT succeeded. |
| E1: free account canary | Passed | Organization API: 500 credits, Seedance 2.5 available, concurrency 1. |
| E2: matched source selection | Prepared | Three recent completed runs have a persisted plan and raw Veo artifact; candidate below. |
| E3: no-spend admission gates | Passed | Real hosted context plus ephemeral in-memory test settings: disabled flag blocks; unauthorized operator blocks; credit confirmation=1 returns 409 with blocked_before_submit=true. Provider/storage factories are guarded and never reached. Evaluation rows remain unchanged. No sentinel credentials were saved. |
| F: paid canary | Provider blocked | One approved 480p/8s/audio request accepted at 160 estimated credits, then third-party moderation failed; actual cost 0 and balance 500. Failure persisted through real poller RPC. |
| G: matched media comparison | Blocked | No Runway output exists. No scores or verdict can be assigned. |

## Hosted database evidence

Migration version: `20261001000000`.
Migration SHA-256: `0020cd63ad89563b9b755b707d383f6b85a74afe4f2da605e64c86f5e3c2e6a0`.

```json
{
  "rpc_count": 10,
  "service_select": true,
  "service_insert": false,
  "service_update": false,
  "service_delete": false,
  "anon_select": false,
  "authenticated_select": false,
  "recorded_versions": 1,
  "notification_queue_usage": 0,
  "evaluation_rows": 0
}
```

No evaluation rows were created. Notification queue usage was zero. Explicit schema-reload request succeeded, and a fresh PostgREST process read the table successfully.

The repo CLI dry run found seven remote versions absent locally. An attempted staging setup failed and its following fetch downloaded hosted migration copies into the repo. All 55 modified tracked migrations were restored from their previously clean Git state; all nine fetch-added files were removed; the three pre-existing untracked migrations were preserved. Subsequent history work used an isolated temp workspace. The isolated CLI dry run still listed two unrelated history-alignment migrations, so the approved Runway migration was applied alone through the official Management query API. Existing hosted history was retained.

Starting Docker restarted local `aiugc-web-1`, `aiugc-semantic-video-worker-1`, and `aiugc-publish-scheduler-1` against hosted Supabase. These were stopped to isolate validation and remain stopped. Temporary test containers were removed after validation. Other application containers were left as found after Docker startup.

## Candidate and open gates

- Post: `15019b38-257e-4f81-98b1-b8ffa9836ffb`.
- Take index: `0`; source run completed, with raw Veo artifact available.
- Resolution/duration: `1080p`, 8 seconds, portrait.
- Estimate: 544 credits, $5.44 before any applicable tax. No credits spent in this validation.
- Real preview currently blocks with feature_flag_disabled, credentials_missing, output_hosts_not_configured, operator_not_authorized.
- Pending user answers: permission to retrieve an existing credential from Runway Developer rather than Davinci; AIUGC operator login email.
- Production enablement and PROD_ENV_FILE_B64 changes remain unapproved and unperformed.
- Paid canary approval should be requested only after the account canary and fully configured preview succeed.

## Official sources checked

[Runway pricing](https://docs.dev.runwayml.com/guides/pricing/) confirms 20/30/68 credits per second of output, 80-credit minimum, and $0.01 per credit. Reference images are free, matching this evaluation's image-only input; reference-video surcharges do not apply.

[Runway output documentation](https://docs.dev.runwayml.com/assets/outputs/) uses `dnznrvs05pmza.cloudfront.net` in an example and states that signed URLs expire. This is an example host, not proof that every live Seedance result uses it. The real output hostname remains unconfirmed.

[Supabase query API](https://supabase.com/docs/reference/api/v1-run-a-query) provides the approved migration and read-only verification path.

## Existing failures from the broader run

These belong to the current working tree outside the Runway validation block and were left unchanged. The full suite also has the collection failure described above.

- `tests/test_blog_scheduling.py::test_blog_cron_dispatch_bypasses_global_auth_with_cron_bearer`
- `tests/test_caption_worker.py::TestProcessCaptionPost::test_full_caption_pipeline`
- `tests/test_caption_worker.py::TestProcessCaptionPost::test_empty_transcript_skips_burn`
- `tests/test_caption_worker_alignment.py::test_caption_worker_keeps_active_backoff_after_poll_error`
- `tests/test_expand_topic_variants.py::test_expand_topic_variants_generates_and_stores`
- `tests/test_lifestyle_generation_regression.py::test_discover_topics_falls_back_for_missing_lifestyle_posts`
- `tests/test_lifestyle_generation_regression.py::test_mixed_batch_lifestyle_dedupe_stays_lane_scoped`
- `tests/test_lifestyle_generation_regression.py::test_discover_topics_reuses_bank_suggestions_without_self_deduping`
- `tests/test_lifestyle_variant.py::test_generate_dialog_scripts_variant_includes_constraints`
- `tests/test_posts_manual_draft_mode.py::test_automated_save_rejects_underlength_32s_lifestyle_script[asyncio]`
- `tests/test_posts_manual_draft_mode.py::test_automated_save_rejects_underlength_32s_lifestyle_script[trio]`
- `tests/test_production_compose_contract.py::test_settings_respect_app_env_file_override`
- `tests/test_production_compose_contract.py::test_production_compose_does_not_include_web_healthcheck`
- `tests/test_production_compose_contract.py::test_hostinger_runtime_does_not_include_web_healthcheck`
- `tests/test_prompt1_variant.py::test_build_prompt1_variant_includes_hook_bank`
- `tests/test_prompt1_variant.py::test_build_prompt1_includes_yaml_hook_bank_for_canonical_path`
- `tests/test_prompt_audit.py::TestBuildProviderPromptRequest::test_sora_returns_optimized_prompt_path`
- `tests/test_tiktok_url_verification.py::test_tiktok_url_verification_file_is_served_at_root`
- `tests/test_topic_prompt_templates.py::test_prompt_text_files_exist_for_all_duration_tiers`
- `tests/test_topic_prompt_templates.py::test_build_prompt1_batch_keeps_rotation_context`
- `tests/test_topic_quality_gate.py::test_validate_pre_persistence_topic_payload_matches_published_bounds_for_all_tiers`
- `tests/test_topic_quality_gate.py::test_validate_pre_persistence_topic_payload_uses_lifestyle_bounds_for_lifestyle_posts`
- `tests/test_topic_quality_gate.py::test_validate_pre_persistence_topic_payload_repairs_short_16s_lifestyle_script`
- `tests/test_topic_researcher_queries.py::test_upsert_topic_script_variants_accepts_product_32s_midrange_script`
- `tests/test_topics_hub.py::test_topics_hub_uses_fixed_height_desktop_panels`
- `tests/test_topics_hub.py::test_prompt_builders_include_bank_and_research_context`
- `tests/test_topics_hub.py::test_topics_hub_html_renders_new_badge_for_fresh_generated_topics`
- `tests/test_topics_hub.py::test_launch_research_with_new_topic_title`
- `tests/test_topics_hub.py::test_launch_redirects_and_hub_shows_active_runs`
- `tests/test_topics_hub.py::test_full_launch_flow_pick_from_list`
- `tests/test_vertex_ai_config.py::test_vertex_settings_default_to_disabled`
- `tests/test_vertex_ai_config.py::test_gemini_provider_accepts_legacy_fallback`
- `tests/test_vertex_ai_config.py::test_vertex_settings_use_explicit_project_and_location`
- `tests/test_vertex_ai_config.py::test_vertex_settings_respect_app_env_file_override`
- `tests/test_video_quota_guard.py::test_generate_all_character_consistency_uses_approved_scene_reference_set_for_segmented_submit`
- `tests/test_video_quota_guard.py::test_generate_all_character_consistency_prepares_lora_scene_reference_set_when_missing`

## Credential and test preparation update

The supplied Runway credential was saved only to ignored local `.env`; a fresh process reports fingerprint `838f2c3eef53`. The free account probe passed: credit balance 500, Seedance 2.5 available, concurrent generation limit 1, daily generation limit 50. No paid request has been sent.

The original completed candidate failed source preflight: its persisted scene-generation contract is v12 while the current uncommitted default is v13; even with the isolated v12 process setting, its frozen visual contract fails validation. It was rejected. Of 50 recent planned runs, two pass both contract validators under the source's v12 generation contract. The selected source below passed full re-download/checksum verification of both actor references, identity gate, approved master, plan, and derived shot frame. Its Veo take is planned but has no raw artifact, so a matched Veo comparison is unavailable.

This canary uses a process-only `SEMANTIC_SCENE_PLATE_CONTRACT_VERSION=flash-identity-independent-qa-global-v12` override matching the approved source; global v13 settings stay unchanged. Current-default validation of legacy sources remains fail-closed.

Selected post: `91825c68-5da5-4b50-b1dc-cee45d90abc3`, take 0, attempt 1. Resolution: 720p, portrait, duration 8 seconds. Estimate: 240 credits / $2.40. The original 1080p estimate of 544 credits exceeds the current balance. The shot image is 5,745,074 bytes, so the pipeline will use its checksum-addressed HTTPS storage transport rather than the inline data-URI limit.

Shot SHA-256: `d3fa8222a65afcba3b33179186b847e8e1d81aa4ab0e6bd551b9fb0c2b1523a6`.
Prompt SHA-256: `6bff943645a9d96189e24a0f3e435b4d305f7573434faa68950983958823927f`.

Operator email selection and explicit approval for this replacement post/take are pending. Paid submission remains gated.


## Final 480p canary result

User approval: “Yes please generate but do it in 480p.” Operator: existing primary account `caposk817@gmail.com`.

- Post: `91825c68-5da5-4b50-b1dc-cee45d90abc3`; take 0, attempt 1, ID `c7ae2444-e51a-45f0-8794-0380923fdb1f`.
- Evaluation: `e01cf409-fb1c-4a8c-900c-ca3d6c7591af`; Runway task: `958ce12a-dec0-407a-898d-4146d0602c68`.
- Request: Seedance 2.5, 480:854 portrait, 8 seconds, audio enabled, exact persisted source image and prompt. Source checksum and immutable references verified. Process-only scene contract pin `flash-identity-independent-qa-global-v12` matches this existing source; pending local v13 changes remain untouched.
- Estimated credits: 160 ($1.60 at configured rate); provider confirmed estimate 160. Actual provider cost: 0; balance 500 before and after.
- Final persisted status: failed, `provider_failed`, provider failure code `INPUT_PREPROCESSING.SAFETY.THIRD_PARTY`. Message: request blocked by model provider content moderation. No output URL returned, no output downloaded, no media scores assigned.
- Quota reservation is terminal failed with 160 consumed admission units and 0 released units, while actual billing is 0. The quota policy conservatively counts provider-accepted requests and only releases proven no-task submissions; these admission units are not a billing claim.
- Baseline digests of `posts.video_*`, complete Semantic run, and complete take list match after finalization. No production delivery fields changed and no Veo request occurred.
- Matched Veo comparison is unavailable: this valid approved source has no generated Veo artifact. Successful output download, ffprobe, storage materialization, and audiovisual quality remain blocked by moderation.
- Persistent feature flag remains false. No retry was attempted. A future source or prompt change requires an explicitly approved new test and must respect provider moderation.

Exact output host was obtained from [Runway official output docs](https://docs.dev.runwayml.com/assets/outputs/); signed provider output URLs and credentials are excluded from this report.


## Revalidation — 2026-10-02

BRIDGECODE_ROUTE: external integration failure → [GENERAL, RESEARCH, EYE] | MODE: Mixed | WHY: Verify the provider boundary and real database lifecycle before claiming generation works.

No new paid generation was submitted. A fresh free organization API request succeeds and still reports 500 credits. The original task remains failed with `INPUT_PREPROCESSING.SAFETY.THIRD_PARTY`, cost 0. Hosted evaluation count remains exactly one, and its persisted status and actual cost match the provider.

Source transport recheck: HEAD 200, PNG content type, declared Content-Length 5,745,074 bytes. GET succeeds with the exact original shot SHA-256. Runway's documented input limit for URL-hosted images is 16 MB; the source dimensions and aspect are inside the Seedance input limits. Output ratio 480:854 and duration 8 seconds are documented Seedance settings. This evidence rules out the observed source hosting/header/checksum mistakes; it does not identify which input moderation rejected.

All production baseline hashes still match: post video fields, complete Semantic run, and complete take list.

Command: `SEMANTIC_UGC_POSTGRES_CONTAINER=aiugc-runway-validation-20261002 .venv/bin/python -m pytest -q tests/test_runway_client.py tests/test_runway_evaluations.py tests/test_runway_evaluation_migration_postgres.py`.
Result: **131 passed, no skips**. Includes the real PostgreSQL lifecycle and privilege checks, HTTP contracts, output download guards, real ffprobe fixtures, and a new regression for the exact third-party moderation failure preserving zero cost without a replacement submission or output materialization. Temporary PostgreSQL container removed after validation. Existing local production-connected consumers remain stopped.

[Runway task-failure guidance](https://docs.dev.runwayml.com/errors/task-failures/) advises against retrying moderation failures. [Runway input requirements](https://docs.dev.runwayml.com/assets/inputs/) confirm the supported transport and ratio. The exact third-party failure does not establish a cause such as a face restriction; no specific cause is claimed and no moderation threshold is changed.

Next external action: ask Runway support to review the failed task and confirm whether the actor-reference use case is supported. Successful source-to-video generation and output storage remain blocked until that boundary is resolved. The feature remains disabled persistently.

Support draft (not sent):

> Please review task `958ce12a-dec0-407a-898d-4146d0602c68`, created on 2026-10-01 using `seedance2_5` through POST /v1/image_to_video. Runway accepted the request and estimated 160 credits, but the task failed with INPUT_PREPROCESSING.SAFETY.THIRD_PARTY. It requested 8 seconds at 480:854 with audio. The PNG source is available over HTTPS with HEAD 200, correct image/png and Content-Length headers, 5,745,074 bytes, and dimensions 1536×2752. Can you identify the unsupported input and confirm whether this synthetic actor-reference workflow is supported? Actual task cost is 0 and the account balance is unchanged. We have not retried the rejected request.


## Root-cause investigation and repeat-spend prevention — 2026-10-02

The original failure identifies third-party input moderation, not a finding that the operator's script is prohibited. Runway's full task response has only `id`, `createdAt`, `status`, `failure`, `failureCode`, and `cost`; there is no additional rejection category or input locator. Computer use inspected the signed-in developer portal's Request History. Submission returned HTTP 200, API version 2024-11-06, and estimatedCost 160 credits. Request ID: `9c2e8374-374b-40b8-9546-c27a1a86f417`. The portal adds no finer task diagnostics; GET response bodies are not stored there.

A local hash/structure inspection confirms the submitted prompt matches the persisted take (3,456 characters) and the negative prompt was omitted. Simple local term checks found no explicit-content, public-figure, or prompt-writing instructions; this is a limited diagnostic, not a moderation verdict. Source transport/checksum and numeric media requirements were verified in the revalidation above.

Primary evidence: [BytePlus portrait asset guide](https://docs.byteplus.com/en/docs/modelark/seedance-portrait-asset-guide), last updated September 28, 2026, loaded and read through the in-app browser. Seedance 2.5 restricts directly uploaded face-containing portraits. The official supported mechanisms are trusted model outputs from specified models on the same ModelArk account, preset digital characters, or verified and authorized real-person asset IDs. Cross-platform outputs are explicitly excluded from that trusted-output mechanism. Our actor/scene source comes from the existing external Gemini workflow and is sent over an R2 HTTPS URL, with no ModelArk portrait authorization. That is a concrete compatibility mismatch with the upstream documented portrait route and a strong explanation for the third-party rejection. Applying that restriction to Runway's exact downstream account remains an inference, because its error does not identify the rejected element. A synthetic portrait must not be claimed to be a verified real-person asset.

No verified Runway API path exposing that ModelArk asset authorization has been established. Switching the production actor, regenerating identity elsewhere, dropping the image, weakening moderation, or changing the provider would change the evaluation contract and would not constitute a matched fix.

Local mitigation implemented: repository uses a bounded, server-filtered read matching post, model, source checksum and prompt hash against previous failed evaluations with a SAFETY code. Preview reports `previous_provider_moderation`; submit returns 409 with `blocked_before_submit=true` before creating a client, uploading a source, reserving credits, or sending provider work. Output resolution changes do not erase the rejected input fingerprint. This prevents repeat spending but does not resolve the upstream portrait compatibility restriction.

Validation: focused suite **133 passed, 1 existing PostgreSQL skip** after this change. New regressions cover blocked preview and submit at 480p, 720p, and 1080p. The previous real PostgreSQL lifecycle run passed 131 tests before this service-only change. Real hosted no-spend validation of the new JSON-path history query and submit guard passes: preview is ineligible solely due to previous moderation; submit is 409; throwing provider/storage factories are never reached; evaluation row count remains one. `git diff --check` passes. No migration, production credential change, new paid submission, or provider fallback occurred.

Updated support draft (not sent): please review request `9c2e8374-374b-40b8-9546-c27a1a86f417` and task `958ce12a-dec0-407a-898d-4146d0602c68`. Is INPUT_PREPROCESSING.SAFETY.THIRD_PARTY caused by the realistic portrait reference? Does Runway expose an approved asset route for externally generated synthetic actor images while preserving that exact identity and frame? If so, please provide the documented endpoint/authorization mechanism. This is an ordinary German actor-presenter workflow, and the image is passed through unchanged from the approved source. No rejected request has been retried.


## Runway character realism image — 2026-10-02

User authorized one direct Runway image generation. Re-downloaded and checksum-verified the same canary character’s canonical frontal and three-quarter references (actor `f151f53c-af60-45e7-b4ba-3f322efb99bb`). The complete literal Raw Camera Casting system contract generated a finished image prompt in a separate writer stage. Only that output went to Runway’s `POST /v1/text_to_image`, model `seedream5_lite`, one 1600×2848 PNG, with both immutable reference images. No image transformation or portrait-moderation bypass was applied.

Task `89e3a4e9-29b3-4870-9a40-e22e30e455b9` succeeded. Actual cost: 4 credits ($0.04); balance 500 → 496. Original PNG: 4,999,819 bytes; SHA-256 `be962400783ba4cdcfc0f39e47b01fc4849b5b56f2721eb17cb5447e13dc843f`. Output host `dnznrvs05pmza.cloudfront.net` passed the configured HTTPS host allowlist. PNG decode, dimensions, and visual inspection passed. Local result: `output/runway-character-realism-2026-10-02/character-realism.png`; sanitized provenance in adjacent `manifest.json`. Character resemblance was visually inspected, without an independent production identity gate.

This standalone image did not change production references, posts, runs, takes, or publishing, and no further video was submitted. Seedance compatibility remains unverified: Runway-hosted reference-guided Seedream output is not documented as equivalent to the upstream ModelArk same-platform trusted text-to-image route.


## Seedream portrait → Seedance video test — 2026-10-02

User explicitly requested video testing after reviewing the Runway-generated character image. Executed exactly one standalone provider compatibility canary: `seedance2_5`, 8 seconds, 480:854, audio enabled, estimated 160 credits ($1.60). The image came from the original successful Seedream task output URL; re-download matched the unedited local PNG checksum. The performance prompt and seed came from the previous take without changing the text; prompt checksum stayed `6bff943645a9d96189e24a0f3e435b4d305f7573434faa68950983958823927f`. This was an isolated new-source diagnostic, not a production Semantic evaluation or delivery replacement. No production database writes occurred.

Task `7f9d1252-ae9f-4356-94a1-417901da7162` was accepted with an estimated 160-credit cost, then failed before generation with `INPUT_PREPROCESSING.SAFETY.THIRD_PARTY`. Actual task cost: 0 credits. Balance stayed 496. No video output exists, so media/playback verification could not run. The durable submission/terminal fence is `output/runway-character-realism-2026-10-02/video-manifest.json`; the terminal task cannot be resubmitted by the test runner.

Conclusion: using an unedited portrait generated through Runway’s Seedream 5 Lite endpoint did not resolve Seedance’s third-party input restriction. This is evidence against treating Runway image generation as sufficient portrait authorization; the exact vendor moderation trigger remains unconfirmed. Stop additional retries for this rejected image/prompt and obtain Runway clarification on supported synthetic portrait/authorized asset inputs. Support messages remain unsent without explicit user authorization.


## Provider escalation packet — 2026-10-02

Current Runway SDK schema confirms `seedance2_5` accepts a string HTTPS prompt image, 480:854, 8-second duration, audio, and a 3,456-character prompt. No missing authorization flag or portrait asset field is exposed in that schema. Runway’s linked [Seedance guide](https://help.runwayml.com/hc/en-us/articles/50488490233363-Creating-with-Seedance-2-0) explicitly describes realistic-human image-input moderation constraints and provider-owned rejection. That guide covers Seedance 2.0 and is linked from the 2.5 model catalog; it corroborates the boundary but does not prove our task-specific trigger. No local payload defect has been established.

### Reviewable support message (not sent)

Subject: Seedance 2.5 rejects synthetic character portraits, including original Runway Seedream output

Please investigate the upstream rejection rule for these two Runway Dev tasks:

- `958ce12a-dec0-407a-898d-4146d0602c68` — externally generated approved synthetic actor frame; request ID `9c2e8374-374b-40b8-9546-c27a1a86f417`.
- `7f9d1252-ae9f-4356-94a1-417901da7162` — original unedited image output from Runway Seedream 5 Lite task `89e3a4e9-29b3-4870-9a40-e22e30e455b9`, accessed through the original output HTTPS URL on the same developer account.

Both were accepted with HTTP 200 and an estimate of 160 credits for `seedance2_5`, 8 seconds, 480:854, audio enabled; both then failed with `INPUT_PREPROCESSING.SAFETY.THIRD_PARTY` and actual cost 0. The performance prompt was identical across tests. The second image is PNG, 1600×2848, 4,999,819 bytes; re-download preserved its SHA-256 exactly. The first image likewise passed HTTPS, size, MIME, aspect and checksum checks. Neither test returned video output.

Can you identify which input and exact downstream policy produced the rejection? Does Runway’s Seedance 2.5 integration support reference-guided photorealistic synthetic adult characters? If it requires a trusted portrait or authorized asset, please provide the documented Runway endpoint and account setup that preserve this exact synthetic identity. Is reference-guided Seedream output excluded from trusted portrait provenance even when obtained through Runway on the same account? We are seeking a supported route, not a moderation override. We have stopped submissions of the rejected inputs.

No credentials, image attachments, prompt text or signed URLs are included in this support packet.

Free diagnostic recheck: both failed tasks still return the identical provider message, “Your request was blocked by this model provider’s content moderation system,” code `INPUT_PREPROCESSING.SAFETY.THIRD_PARTY`, cost 0, and no output. Account probe confirms Seedance enabled, balance 496, and two counted video generations. Original Seedream output HEAD with Runway API user-agent: 200, image/png, Content-Length 4,999,819, no redirect; HTTPS URL length 405. This rules out those transport/header/size mistakes for the second canary. Local focused validation: `.venv/bin/python -m pytest -q tests/test_runway_client.py tests/test_runway_evaluations.py` → 132 passed. No further provider submission or code change was made. Exact rejection rule and supported portrait authorization remain external information, requested in the prepared support message.


## Support escalation submitted — 2026-10-02

User explicitly authorized contacting Runway support. Computer use opened the authenticated Runway Dev support chat, sent the prepared non-secret packet with both failed tasks and successful image task, and requested human technical/API review. The automated agent stated Runway does not receive the exact downstream rejection rule and could not confirm a documented identity-preserving synthetic portrait route. This is an automated support answer, not a task-specific human determination.

Completed the support form using the existing account email, category Content removed or flagged, incident Today, impact My work is blocked, diagnostic summary, and task IDs. Submission succeeded. Chat explicitly confirmed the ticket was created and that a specialist would follow up by email; no ticket number was supplied. No API credentials, prompt text, signed URLs, or image attachments were sent. Saved screenshot proof at `output/runway-character-realism-2026-10-02/support-submitted.png` and sanitized status at `support-escalation.json`. No new generation request or production change occurred. The generation fix remains pending provider clarification.

## Seedance-only requirement and supported asset research — 2026-10-02

BRIDGECODE_ROUTE: Seedance 2.5 portrait admission failure → [GENERAL, RESEARCH, EYE] | MODE: Mixed | WHY: Establish a supported portrait authorization mechanism while preserving the required model and character.

The user explicitly requires Seedance 2.5; another video model cannot satisfy completion. A prior WAN3 diagnostic task `da1f73f6-2efd-4cfb-86da-2a6c38ac3565` succeeded at 40 credits ($0.40), but is rejected as a solution and its generation code/tests were removed. Its local billing evidence is retained without changing production state. The Runway Seedream image cost 4 credits ($0.04); both Seedance attempts cost 0. Last observed account balance after those tasks: 456 credits. No further generation was submitted. The independent QA utility created during that detour was also removed from this repository.

Restored adapter and evaluation runtime have no alternative-model submission or fallback. PostgreSQL still enforces `seedance2_5`, `runway_seedance_2_5`, eight-second duration, and approved portrait ratios. Latest focused result after restoration: `.venv/bin/python -m pytest -q tests/test_runway_client.py tests/test_runway_evaluations.py` → **132 passed**. An independent read-only review confirmed these boundaries. The persistent feature flag remains false. Existing moderation history blocks another submission of the rejected exact source/prompt at every output resolution. Correction memory now preserves the operator-required model throughout debugging.

The dedicated [Runway Seedance 2.5 guide](https://help.runwayml.com/hc/en-us/articles/53542207042323-Creating-with-Seedance-2-5) includes human image-reference examples. Consequently, the earlier linked 2.0 article does not prove a blanket ban on all portrait inputs in 2.5. The confirmed diagnosis remains third-party input preprocessing rejection. Portrait provenance/authorization is a strong compatibility hypothesis; the task response does not identify its exact trigger, and using the same prompt for both images did not independently isolate image and prompt causes.

BytePlus now documents contracts for a distinct synthetic-character authorization mechanism; its end-to-end use for this custom character remains unverified:

1. Complete enterprise/organization verification, applicable asset authorization terms, and activate the free Advanced Creation Rights Entry tier, which includes virtual portraits through Console/API. [Official purchase guide](https://docs.byteplus.com/pt/docs/ModelArk/seedance-2-0-purchase-guide)
2. With AK/SK authentication, create an asset group with `GroupType: AIGC` under a fixed project, after signing the console's virtual-asset authorization letter. This differs from real-person `LivenessFace` verification. [CreateAssetGroup](https://docs.byteplus.com/en/docs/modelark/create-asset-group-api?redirect=1)
3. Register the unchanged original image using `CreateAsset`, `AssetType: Image`, and the matching group/project. Keep normal moderation; poll `GetAsset` until `Active`. Our existing 1600×2848, approximately 5 MB PNG fits the documented image limits, but asset approval has not been tested. [CreateAsset](https://docs.byteplus.com/en/docs/modelark/create-asset-api?redirect=1)
4. Seedance 2.5's generic generation API accepts digital-character `asset://<asset_ID>` inputs. The shared real-human library guide requires asset/inference project alignment; applying that architecture to custom AIGC assets is an inference. The custom virtual guide is gated and has not been read or tested, so custom AIGC asset → Seedance generation is a supported-contract hypothesis pending access/provider confirmation. [Create video generation task API](https://docs.byteplus.com/zh-CN/docs/modelark/create-video-generation-task-api), [Seedance 2.5 tutorial](https://docs.byteplus.com/fr/docs/modelark/seedance-2-5?redirect=1), [project isolation in real-human library guide](https://docs.byteplus.com/en/docs/modelark/guide-preview?redirect=1)

Runway's current [OpenAPI](https://github.com/runwayml/openapi/blob/main/openapi.json) and [Python SDK schema](https://github.com/runwayml/sdk-python/blob/main/src/runwayml/types/image_to_video_create_params.py) expose no AIGC asset registration, upstream project, or portrait authorization fields; their Seedance image URIs accept HTTPS, `runway://`, or data inputs. Ephemeral Runway uploads are transport and do not establish this authorization. A supported Runway-to-ModelArk asset mapping, or separate verified direct ModelArk account access, is the unresolved dependency. No undocumented field, asset URI coercion, filter relaxation, or new rejected-source retry was attempted.

The signed-in Runway support chat confirms the existing specialist case was created. A scoped Gmail search for Runway/Decagon replies since 2026-10-01 found none. A follow-up specifying the AIGC/project bridge is prepared but **not sent**: computer-use automatic approval review rejected further Chrome accessibility access after the active window changed to an unrelated private site. The user was asked to bring the existing Runway support tab back to the front. The BytePlus public guide displayed a legal-terms dialog; no agreement or account onboarding was accepted. The user was also asked whether verified BytePlus business/asset access already exists.

Completion remains blocked on authoritative portrait authorization and a successful Seedance output. No Seedance media exists to score, preview, upload, caption, or compare. The integration and repeat-spend protections are validated; eight-second Seedance generation has not been claimed as working.


## New fictional character, native Seedance generation — 2026-10-03

BRIDGECODE_ROUTE: new-source Seedance compatibility test → [GENERAL, RESEARCH, EYE] | MODE: Mixed | WHY: Test a different character through a documented native input while retaining Seedance 2.5 and the eight-second 480p setup.

The user explicitly authorized exploring a new character/image. The current [Runway OpenAPI](https://github.com/runwayml/openapi/blob/main/openapi.json) supports `seedance2_5` on `POST /v1/text_to_video` without image references. Submitted one native fictional-adult presenter canary: 8 seconds, 480:854, audio enabled, original take seed, and the exact requested 15-word German dialogue. Visual directions were adapted for native generation; this is not an exact original-prompt/source comparison. No rejected image was reused, no image moderation was relaxed, and no alternative video model was submitted.

Task `bfb930e9-9fe8-40ed-8a63-e2a609e20607` succeeded. Actual cost **160 credits ($1.60)**; balance 456 → 296. Output host `dnznrvs05pmza.cloudfront.net` passed the existing HTTPS download allowlist and byte cap. Original `seedance-8s-480p.mp4`: 3,371,440 bytes, SHA-256 `9cc1eecd79ecbe044d662e3f3d6c2c53ee44911dfe866c493a7f8b7b6a4b6612`, H.264 480×854, 24 fps, 193 frames, video 8.041667 seconds, AAC/container 8.064 seconds. Original bytes are preserved.

`seedance-exact-8s-480p.mp4` removes the single excess provider frame and bounded AAC padding without speech retiming or replacement audio. ffprobe independently confirms **8.000-second video, audio and container**, 192 frames, 480×854 at 24 fps. SHA-256 `421fe4dfd2ce8645abdd73708299ebbe7ce8dafe3004b328eeaf85a923082ab1`; 1,665,934 bytes. Both speech checks place the final recognized word at 7.72 seconds. Sampled frames show a continuous photorealistic kitchen presenter with stable face, blue clothing and room, no visible text or watermark. Full audiovisual lip-sync and production identity/terminal-speech gates were not independently passed. The exact file was opened through Codex's native media panel (queued).

Offline faster-whisper base and small independently recognize German with probabilities 0.9971 and 0.9936, respectively. Both recognize 14 words against 15 requested, WER **0.20**, and fail exact normalized matching. The existing take transcript evaluator also fails with `word_error_rate_exceeded`, despite matching first and final words. Thus the generated clip proves model/account/API generation, but **does not satisfy verbatim-dialogue production acceptance**. Safe QA reports are adjacent to the output; raw transcripts and expected dialogue remain private temporary files and were never uploaded for ASR.

The standalone runner atomically persists paid submission intent before HTTP entry, fences submitted/ambiguous/terminal tasks against resubmission, and leaves production storage/database projections untouched. Read-only baseline hashes confirm unchanged post video fields, Semantic run and takes. No evaluation row, captions, publishing change or deployment was created; persistent evaluation flag stays false. Focused runner regression checks: `.venv/bin/python -m pytest -q tests/test_runway_new_character_canary.py` → **12 passed**. No new project dependency was added; local ASR used a disposable temporary environment.

An optional text-only Seedream-image pipeline was prepared but not run. Automatic approval review rejected sending the complete internal Raw Camera Casting prompt to Gemini without explicit payload-export authorization; the approval question remains unanswered. The successful native Seedance path made no Gemini or Seedream call and required no such export. There was exactly one paid task this turn.

Conclusion: native Seedance 2.5 generation with a new character works. Reference-image authorization remains a separate unresolved provider boundary, and exact German dialogue fidelity requires further validation before production use. A future supported reference-audio canary could test speech fidelity, but would be a separately priced paid request; none was submitted here.


## Fresh AYRA wheelchair sheet-only reference attempt — 2026-10-03

BRIDGECODE_ROUTE: fresh operator-authorized reference generation → [GENERAL, RESEARCH, EYE] | MODE: Mixed | WHY: Submit only the new wheelchair sheet through Seedance's supported reference endpoint while preserving the credit and duplicate-submission controls.

The user explicitly authorized one fresh eight-second 480p Seedance 2.5 attempt using only the finalized AYRA wheelchair sheet, and confirmed the earlier established German test script as the working H1 interpretation. Added `RunwayClient.submit_reference_to_video` for `POST /v1/text_to_video`, with `references: [{uri: <new-sheet-data-URI>}]` and no keyframes, legacy images, video/audio references, or alternative model. Added `scripts/run_runway_sheet_canary.py`: explicit 160-credit confirmation, operator allowlist, sheet/script hash guards, moderation fingerprint lookup, durable local submission intent, RPC credit reservation/admission/acknowledgement, scoped lease polling and the existing evaluation output processor. It is a separately authorized CLI canary, leaving the disabled legacy single-shot UI path unchanged. No schema migration, actor mutation, post delivery mutation or deployment occurred.

Actual sole image input: `output/ayra-seedance-character-sheet-2026-10-03/ayra-wheelchair-nine-view-character-sheet.png`, PNG 1536×1024, 2,415,663 bytes, SHA-256 `03bb788f0ec1cf899694ba1ffd43fb494c7aeebf751a0ea273d694b72828d609`; inline data URI 3,220,906 characters within the 5,242,880 limit. Original actor portraits and old scene/Seedream images were not loaded or submitted. The saved earlier take supplies dialogue/seed only. Prompt binds @Image 1 to the sole actor, source wardrobe and wheelchair, and requires one continuous seated scene instead of a sheet layout.

Evaluation `4a911e91-5975-4382-9d8d-1e75f8ec0ea8`; Runway task `8a6c0a34-71ac-401f-b775-ce09b149f1a3`. Accepted with estimated 160 credits, then **FAILED** at input preprocessing with **`INPUT_PREPROCESSING.SAFETY.THIRD_PARTY`**. Provider actual cost **0 credits**, balance **296 → 296**, no output. Persisted evaluation status is failed with actual_credits=0 and matching failure code. Fingerprint lookup finds this failed sheet/prompt, and the durable runner rejects another submit after entered intent. Read-only baseline hashes confirm unchanged production post video fields, Semantic run and takes. No video exists to preview, score, normalize, or copy to voice outputs.

Quota audit found a separate existing accounting limitation: acknowledgement consumes the estimated 160 units; terminal failure records actual_credits=0 but the shared ledger remains status=failed, reserved_units=160, consumed_units=160, released_units=0. It holds no active generation slot, but conservatively overcounts daily quota despite the independently verified zero provider charge. No direct ledger write or unapproved hosted migration was attempted. Evidence: `output/runway-ayra-sheet-h1-2026-10-03/manifest.json` and `terminal-verification.json`.

Focused validation: `.venv/bin/python -m pytest -q tests/test_runway_client.py tests/test_runway_evaluations.py tests/test_runway_sheet_canary.py` → **136 passed**. Request-route test asserts one unpositioned reference, Seedance 2.5, 8 seconds, portrait 480p, and no old keyframe/video/audio fields. Runner tests protect exact saved dialogue and changed sheet/script inputs.

The correctly formed fresh reference request still receiving the same upstream safety rejection demonstrates that missing character-sheet fields was not a proven explanation of the earlier moderation failures. Supported API reference syntax and this particular portrait's admission are separate questions. The exact upstream moderation trigger remains undisclosed. This task is terminal: no retry, modified-sheet workaround, alternative-model submission or new support contact is authorized by this result. A successful image-conditioned Seedance video remains provider-blocked.

## Erneute Ursachenrecherche — 2026-10-03

BRIDGECODE_ROUTE: wiederholte Seedance-Ablehnung → [GENERAL, RESEARCH, EYE] | MODE: Mixed | WHY: Offizielle Porträtregeln mit den drei realen Referenzfehlern vergleichen, ohne weitere Generierung.

Bestätigt: Drei unterschiedliche Referenzquellen (externes Szenenbild, Runway-Seedream-Bild, neuer AYRA-Rollstuhl-Charakterbogen) scheiterten nach Annahme mit `INPUT_PREPROCESSING.SAFETY.THIRD_PARTY`; die referenzlose Seedance-2.5-Generierung gelang. Das grenzt den Fehler auf den referenzführenden Verarbeitungspfad ein, isoliert jedoch Bild und Prompt nicht vollständig. Die jüngste Aufgabe kostete beim Provider 0 Credits; die interne Quota zählt weiterhin konservativ 160 geschätzte verbrauchte Einheiten. Keine erneute Einreichung, Datenbankänderung oder externe Kontaktaufnahme in dieser Recherche.

Die aktuelle [BytePlus-Porträtanleitung](https://docs.byteplus.com/de/docs/modelark/seedance-portrait-asset-guide?redirect=1), zuletzt aktualisiert am 28.09.2026, nennt Seedance 2.5 ausdrücklich. Direkt hochgeladene Referenzen mit realen menschlichen Gesichtern sind eingeschränkt. Beschrieben werden vertrauenswürdige Originalausgaben im selben ModelArk-Konto, vorgegebene digitale Figuren und autorisierte reale Personen. Für vertrauenswürdige Ausgaben gelten 30 Tage; plattformübergreifende Herkunft ist ausgeschlossen. Die Seedream-Ausnahme nennt ausdrücklich Text-to-Image. Unser früherer Runway-Seedream-Versuch war referenzgeführt; seine Erzeugung bei Runway beweist außerdem keine ModelArk-Konto-/Trust-Zuordnung. Eine realistische synthetische Identität ist nicht automatisch ein freigegebenes Porträt. Ob der konkrete AYRA-Bogen genau diese Prüfung ausgelöst hat, bleibt unbestätigt.

Technikgegenprobe: [Runways offizielle Eingaberegeln](https://docs.dev.runwayml.com/assets/inputs/) erlauben PNG, Base64-Data-URIs bis 5 MB codiert und Referenzseitenverhältnisse 0,4–4 für Seedance 2.5. Der Bogen liegt mit 1536×1024 und 3.220.906 Data-URI-Zeichen darin. Das [offizielle SDK-Schema](https://github.com/runwayml/sdk-python/blob/main/src/runwayml/types/text_to_video_create_params.py) definiert `seedance2_5`, `480:854` und `references` mit `uri`; unsere transportseitige Form entspricht diesem Vertrag. HTTPS und Data-URI scheiterten gleichermaßen. Somit sind Dateigröße, Hosting und fehlende Referenzfelder keine durch die vorhandenen Befunde gestützten Hauptursachen. Annahme einer Aufgabe beweist noch keine erfolgreiche nachgelagerte Medienverarbeitung.

Runways [2.5-Anleitung](https://help.runwayml.com/hc/en-us/articles/53542207042323-Creating-with-Seedance-2-5) unterstützt Personenreferenzen und Reference-Modus grundsätzlich. Die [2.0-Anleitung](https://help.runwayml.com/hc/en-us/articles/50488490233363-Creating-with-Seedance-2-0) beschreibt speziell Moderationsbeschränkungen realistischer menschlicher Bildinputs; das ist ergänzende Evidenz, kein Beweis einer identischen 2.5-Regel. Keine offizielle Quelle in dieser Recherche belegt ein generelles Verbot von Mehrfachansichten, Rollstühlen oder unserem deutschen Dialog. Promptauslösung, Gesichtsfehlklassifikation und ein unbekannter accountseitiger Porträtzugang bleiben möglich; Konto/Schlüssel/Modellzugang insgesamt funktionieren nach dem erfolgreichen Textversuch.

Störungsprüfung: Die offizielle Runway-Statusseite einschließlich History/API war im Webwerkzeug nicht erreichbar. Sekundärarchive melden einen behobenen Seedance-Falschpositiv-Vorfall vom 19.08.2026; das belegt keinen aktuellen Vorfall und erklärt unsere Oktober-Aufgaben nicht. Keine aktuelle Störungsursache behaupten. Runways [Fehlerdokumentation](https://docs.dev.runwayml.com/errors/task-failures/) dokumentiert den exakten THIRD_PARTY-Safety-Suffix nicht ausreichend, um Bild, Prompt oder Unterregel sicher zu identifizieren; Diagnosecodes sind allgemein nur Hinweise.

Unterstützter nächster Schritt: Für die unveränderte AYRA-Identität zunächst den providerseitigen Porträt-/Assetzugang feststellen. BytePlus dokumentiert eine separate synthetische Assetklasse `AIGC`, AK/SK-Authentifizierung, Projektzuordnung und eine erforderliche Autorisierung im [CreateAssetGroup-Vertrag](https://docs.byteplus.com/en/docs/modelark/create-asset-group-api?redirect=1); die private digitale Bibliothek ist dort als zugangsbeschränkt bezeichnet. Das ist eine dokumentierte Registrierungsrichtung, noch kein verifizierter End-to-End-Zugang für unseren Bogen. Reale Personen benötigen den [Verifikations- und Einwilligungsprozess](https://docs.byteplus.com/id/docs/modelark/upload-real-person-portrait-assets). Das geprüfte Runway-Seedance-Schema veröffentlicht keine entsprechende Porträtregistrierung oder ModelArk-Projektbrücke. Daher muss Runway den unterstützten Weg für extern erstellte synthetische Identitäten bestätigen, oder ein berechtigtes direktes ModelArk-Konto muss die passende digitale Assetbibliothek bereitstellen. Keine generischen Personen aus der [Preset-Bibliothek](https://docs.byteplus.com/es/docs/modelark/avatar-library) als AYRA-Ersatz verwenden.

Präzise offene Anbieterfrage, nicht gesendet: Welche Eingabe/Regel verursachte Aufgabe `8a6c0a34-71ac-401f-b775-ce09b149f1a3`, und welcher veröffentlichte Runway-Mechanismus registriert eine externe synthetische Identität für Seedance 2.5, einschließlich erforderlichem Asset-/Projektbezug? Ohne diese Information ist eine bestimmte Prompt- oder Bildänderung als Lösung unbelegt.

Runway-spezifischer Nachtrag: Die vollständige Anforderungsmatrix und eine nicht gesendete taskbezogene Supportdiagnose liegen in `output/runway-ayra-sheet-h1-2026-10-03/runway-requirements-review.md`. Es wurde keine unerfüllte technische Runway-Pflicht gefunden. Runway dokumentiert keine verpflichtende ModelArk-AIGC-Registrierung für seinen Seedance-Vertrag; die Porträtfreigabe bleibt eine externe Hypothese. Temporäre Runway-Uploads sind Dateitransport. Als weiterer dokumentierter Seedance-Zugang wurde fal Reference-to-Video gefunden, mit lokal vorhandenem, noch ungeprüftem Schlüssel; die Zulassung unseres bereits abgelehnten Bogens ist auch dort nicht bestätigt. Kein Providerwechsel oder erneuter Inputversuch wurde ausgeführt. Eine konkrete Veo-3.1-Standard-Option ist nur als nicht autorisierter Modellwechsel beschrieben.
