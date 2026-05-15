# Seeding stress run — 2026-05-13T12:58:04

- target: `POST /batches` with `{value: 5}` × 5 runs
- tier: 8s
- completed: 5 / 5
- failed: 0
- other (coverage timeout / wall timeout): 0

## Per-run

| # | outcome | posts | dur | detail |
|---|---|---|---|---|
| 1 | completed | 5/5 | 53.3s | 5 posts are ready. Opening script review next. |
| 2 | completed | 5/5 | 30.1s | 5 posts are ready. Opening script review next. |
| 3 | completed | 4/5 | 61.9s | 4 posts are ready. Opening script review next. |
| 4 | completed | 5/5 | 5.2s | 5 posts are ready. Opening script review next. |
| 5 | completed | 4/5 | 43.8s | 4 posts are ready. Opening script review next. |