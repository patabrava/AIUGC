# Duplicate Issues

Current issues observed while debugging batch seeding and topic selection:

1. Batch seeding can still collapse multiple scripts onto the same topic family.
   - Root cause: the selector returned multiple rows that were semantically different in text but mapped to the same `canonical_topic`.
   - Mitigation added: batch-level family dedupe in `app/features/topics/handlers.py`.

2. Topic selection can still over-concentrate on a small topic bank.
   - Root cause: `topic_scripts` contains 164 script rows but only 55 distinct `topic_registry_id` values, so the bank has many variants but limited topic diversity.
   - Effect: batches that request 3-4 posts can run out of distinct coverage after dedupe.

3. Topic warmup can fail during batch recovery because `handlers.py` referenced `_WARMUP_SEED_TOPIC_COUNT` without defining it locally.
   - Effect: the batch recovery loop throws a `NameError` and retries repeatedly.
   - Mitigation added: define `_WARMUP_SEED_TOPIC_COUNT = 3` in `app/features/topics/handlers.py`.

4. Warmup can become slow when duplicate filtering leaves no candidates and the code falls back to live Gemini Deep Research.
   - Effect: 8-second and 32-second batches can take a long time to assemble because they wait through multiple research attempts and fallback retries.
   - Likely root cause: the bank coverage for unique topics is too narrow for the requested batch mix, especially after dedupe filters duplicates aggressively.

5. Some warmup paths still depend on live provider availability and timeout behavior.
   - Effect: the system can spend significant time recovering or re-researching even when the bank already contains many script variants.
   - Recommendation: add an early coverage check before seeding so warmup happens once up front, not after selection failure.

6. The live logs show repeated duplicate-topic filtering on the same topic families.
   - Effect: repeated batch retries and fallback lane persistence instead of quick post creation.
   - Recommendation: improve pre-selection coverage checks and, if needed, seed more distinct topic families in the bank.

7. There is a separate backend/UI noise issue during recovery:
   - The app logs repeated status recovery attempts while the batch is still unresolved.
   - This makes it harder to distinguish a genuine new failure from a retry loop.

Files most relevant to these issues:
- `app/features/topics/handlers.py`
- `app/features/topics/queries.py`
- `app/features/topics/bank_warmup.py`
- `app/features/topics/deduplication.py`
