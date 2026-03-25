# Veo Extension Summary

## What Was Built

The Veo pipeline was extended from a fixed 8-second flow to a duration-tier flow:

- `8` seconds uses the original short path.
- `16` and `32` seconds use the extended Veo 3.1 path.

The extended path was designed to chain multiple Veo generations together so the output can reach the requested length without changing the legacy short flow.

## Core Changes

### Duration routing
- The batch/video flow now derives the execution path from the requested duration tier.
- The user selects duration only; the system chooses the pipeline automatically.
- Legacy 8-second behavior remains intact for backward compatibility.

### Prompt shaping
- The original script prompt was kept as close as possible to the short-flow version.
- The prompt builder was updated to support sentence-aware segmentation.
- Extended hops now consume the next sentence chunk instead of replaying the full script.
- The prompt contract was tightened to keep German speech stable and reduce restart/jump behavior.

### Worker chaining
- The worker now tracks extension hops and advances segment state correctly.
- The first extension no longer restarts from the beginning of the script.
- Chain state is stored in `video_metadata`, including hop counts and operation ids.

### Duration verification
- Final acceptance depends on the actual downloaded MP4 length, not only on hop counting.
- The `mvhd` duration parsing bug was fixed so valid videos are not rejected as mismatched.

## Root Causes We Found

### 1. Chopped script source
The long script was being chopped before video generation because the topic/script pipeline could trim the tail into an incomplete sentence. That malformed text then propagated into the prompt builder and extension chain.

### 2. Segment restart bug
The live worker could reuse the wrong segment index on extension, which caused the script to restart at the beginning instead of continuing to the next sentence.

### 3. Duration parser bug
The final MP4 could be valid, but the worker’s duration parser misread the metadata and treated the file as a mismatch.

## Verification That Happened

- The 16s/32s routing tests passed.
- The prompt contract tests passed.
- The live 32-second chain was eventually completed successfully after the worker and duration parser fixes.

## Important Implementation Files

- [app/features/posts/prompt_builder.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/posts/prompt_builder.py)
- [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py)
- [app/features/videos/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py)
- [app/features/videos/schemas.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/schemas.py)
- [app/features/batches/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/batches/handlers.py)
- [tests/test_video_duration_routing.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_video_duration_routing.py)
- [tests/test_veo_prompt_contract.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_veo_prompt_contract.py)
- [tests/test_video_poller_batch_transition.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_video_poller_batch_transition.py)

## Current Status

The Veo extension work is documented and the 16s/32s path was implemented with the goal of preserving the original 8-second pipeline.
