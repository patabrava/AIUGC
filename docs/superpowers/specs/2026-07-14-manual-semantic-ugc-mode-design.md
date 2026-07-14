# Manual Semantic UGC Mode Design

Date: 2026-07-14
Status: Approved for implementation

## Goal

Expose the complete long-form Semantic UGC workflow in batch creation for both automatically generated scripts and manually authored scripts. Both variants accept an exact integer target duration from 8 through the configured Semantic UGC maximum, currently 60 seconds, and route through the same Veo 3.1 planning, approval, generation, quality, composition, and caption pipeline.

## Root cause

The first production implementation modeled Semantic UGC as a single `semantic_ugc` creation mode whose contract included automated topic and script generation. Manual creation remained explicitly outside that design. Exact string comparisons then spread across form visibility, request validation, persistence, database constraints, batch detail rendering, and semantic-service authorization. As a result, selecting a manual mode necessarily returned the user to legacy duration behavior, and no manual semantic mode could be persisted or processed.

## Mode contract

The system supports two semantic creation modes:

- `semantic_ugc`, shown as `Semantic UGC - Veo 3.1`, generates topics and scripts automatically.
- `manual_semantic_ugc`, shown as `Manual Semantic UGC - Veo 3.1`, creates blank draft posts for user-authored scripts.

Both modes:

- persist `video_pipeline_route = 'semantic_ugc'`;
- require `target_duration_seconds` and set `target_length_tier = NULL` on the batch;
- snapshot the active actor using the Semantic UGC reference contract;
- remain outside legacy `CHARACTER_CONSISTENCY_MODES` and Magnific/LoRA routing;
- render the Semantic UGC planning and approval UI on batch detail;
- are accepted by the semantic video service and dedicated worker.

Only `manual_semantic_ugc` is a manual creation mode. It requires `manual_post_count`, does not require post-type counts, creates that many blank drafts, skips topic discovery, and enters `S2_SEEDED`. A compatibility value of 8 may be stored in each draft post's legacy seed metadata, but the batch's authoritative duration remains `target_duration_seconds`.

## Batch form behavior

The mode selector places the automated and manual Semantic UGC choices together near the top. Selecting either displays a numeric `Target video duration` control with 8, 16, 32, and 50 second presets plus any whole second through the configured maximum. Selecting the manual semantic variant also displays `Manual Draft Count` and hides post-type counts. Legacy modes retain their current fixed or automatic duration behavior.

## Persistence and compatibility

A forward-only migration extends the batch creation-mode and duration-authority CHECK constraints to recognize both semantic modes. Existing rows are unchanged. Central predicates define semantic-route membership and manual-mode membership so future code does not repeat exact string checks.

## Validation

Regression coverage must prove schema validation, form parsing, persistence, duplication, manual draft creation, semantic service authorization, database constraints, and conditional form rendering for both semantic variants. Existing Semantic UGC, legacy Manual, and Character Consistency behavior must remain green. A real browser check must confirm that selecting `Manual Semantic UGC - Veo 3.1` reveals both manual draft count and a 50-second target while hiding the legacy 8/16/32 selector.
