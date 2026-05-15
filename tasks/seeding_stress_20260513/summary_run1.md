# Seeding stress run — 2026-05-13T12:42:28

- target: `POST /batches` with `{value: 5}` × 5 runs
- tier: 8s
- completed: 2 / 3
- failed: 1
- other (coverage timeout / wall timeout): 0

## Per-run

| # | outcome | posts | dur | detail |
|---|---|---|---|---|
| 1 | completed | 5/5 | 5.7s | 5 posts are ready. Opening script review next. |
| 2 | completed | 5/5 | 68.2s | 5 posts are ready. Opening script review next. |
| 3 | failed | 1/5 | 20.9s | The seeding run failed before script review could start. |