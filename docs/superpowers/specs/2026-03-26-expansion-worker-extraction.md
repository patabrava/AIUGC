# Separate Script Bank Expansion Worker

## Goal

Extract script bank expansion from the video poller into its own standalone worker so video polling is never blocked by Gemini calls.

## Architecture

The video poller currently runs script bank expansion synchronously in its 10-second polling loop. When expansion runs (every 24 hours), it makes Gemini API calls for 60+ topic registries, blocking video polling for 5+ minutes. This causes videos to sit unprocessed.

The fix: move expansion to a new `workers/expansion_worker.py` that runs independently. The video poller becomes a tight loop that only polls videos and checks batch transitions.

## Changes

### New: `workers/expansion_worker.py`

Standalone worker with a 24-hour cycle:
1. On startup: run `expand_script_bank()` immediately
2. Sleep 24 hours
3. Repeat

Uses the same expansion logic from `app/features/topics/variant_expansion.py` — no changes to the expansion code itself.

Error handling: catch exceptions, log, continue loop (retry on next cycle).

### Modify: `workers/video_poller.py`

Remove all expansion-related code:
- `EXPANSION_INTERVAL_SECONDS` constant
- `EXPANSION_MAX_SCRIPTS_PER_RUN` constant
- `expand_script_bank` function call in the main loop
- Expansion-related imports (variant_expansion, topic queries)
- The `last_expansion_time` tracking variable and all associated logic

The poller's main loop becomes: poll videos → check batch transitions → sleep 10s.

### No changes to:
- `app/features/topics/variant_expansion.py` (expansion logic stays as-is)
- `workers/caption_worker.py` (unrelated)
- Any templates or handlers

## Running

```bash
# Terminal 1: API server
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2: Video poller (fast, never blocked)
python workers/video_poller.py

# Terminal 3: Caption worker
python workers/caption_worker.py

# Terminal 4: Expansion worker (optional, runs once per day)
python workers/expansion_worker.py
```

## Success Criteria

- Video poller poll cycle stays under 2 seconds (no more 5-minute blocks)
- Script bank expansion continues to work with same output as before
- Expansion worker can be started/stopped independently without affecting video polling
