# Deep Research Refactor

## Status Quo

The current architecture couples batch creation directly to Deep Research execution.

What happens today:
- A new batch is created.
- The seeding path immediately starts a fresh Deep Research run for that batch.
- The run is parameterized by the requested counts and the batch context.
- The resulting candidates are deduplicated only after research returns.
- If the batch is restarted or the app is reloaded, the in-memory progress can be lost and the batch can remain stranded in `S1_SETUP`.

Operational consequences:
- Every new batch triggers a new provider run, even when the underlying topic space is already known.
- Duplicates are created repeatedly because the research step is repeated before deduplication can help.
- The system spends provider budget on overlapping discovery instead of reusing durable topic candidates.
- A stalled or interrupted run can leave a batch with no durable recovery path unless status/startup recovery requeues it.

The core architectural issue is that the current unit of work is the batch, but the real reusable unit should be the topic pool.

## Recommendation

Split the system into two layers:

1. Shared topic harvest
- Run Deep Research on a schedule, not per batch.
- Persist normalized topic candidates into a durable topic bank.
- Tag each candidate with category, angle, hooks, language, target duration bands, freshness, and dedupe metadata.
- Use this bank as the primary source of truth for future batch assembly.

2. Batch-specific assembly
- When a user creates a batch, assemble posts from the topic bank first.
- Select topics by post type and requested duration tier.
- Do not start Deep Research by default during batch creation.
- Only fall back to live research if the bank does not have enough coverage for the requested batch.

Why this is better:
- Reduces duplicate topic generation.
- Lowers provider cost.
- Makes batch creation faster and more deterministic.
- Improves recovery because the topic bank is durable.
- Separates content ingestion from batch assembly, which is a cleaner architectural boundary.

## Practical Hybrid

The best near-term implementation is a hybrid:
- Daily scheduled harvest for the common topic pool.
- On-demand Deep Research only when the pool cannot satisfy a batch request.
- Batch assembly remains deterministic and retries against the durable pool first.

This keeps freshness available when needed, but prevents every batch from becoming a new provider research job.

## Decision Summary

If the goal is to minimize duplicates and make topic generation reliable, the architecture should move away from batch-triggered Deep Research and toward a persistent shared topic bank with scheduled harvesting plus batch-level fallback.
