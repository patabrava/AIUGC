# Veo 3.1 Lite 32s Extension Handoff

## Context

This handoff captures the root-cause analysis, code changes, simulation, and live verification for the batch:

- Batch ID: `cc027a62-5e1f-48c8-b6be-7c5ea7858258`
- Symptom: longer VEO videos started repeating a script fragment and overlapping the whole sequence
- Target contract: `32s = 8 + 7 + 7 + 7`
- Live provider surface used for verification: Vertex AI + Supabase + Cloudflare R2

The live verification in this session was run from the patched local checkout. The public `lippelift.xyz` runtime has not been deployed from this patch unless another agent does that separately.

## Root Cause

The base 32s chain was not wrong because of duplicated prompts. The problem was the segment packing strategy.

Before the fix, `_pack_veo_segments_for_profile()` could break the script by raw word count, which produced extension hops that started mid-sentence or mid-clause. That is exactly the kind of input that makes a continuation model repeat prior words instead of moving the story forward.

Concrete examples from the pre-fix live batch were fragments like:

- `Deutschland ... besonders für`
- `... Mit`
- `... Rollstuhl`

Those fragment starts are unsafe for chained continuation prompts.

The second boundary I found was model routing. The batch was using `veo-3.1-lite-generate-001` for base requests, but the Vertex extension hop path was not explicitly passing the requested model, so the hop could fall back to the adapter default.

## What Changed

### 1. Segment packing now respects full dialogue units

Changed in [app/features/videos/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py#L652)

- Replaced raw word splitting with dynamic programming over complete dialogue units.
- Kept the 32s chain on `8 + 7 + 7 + 7`.
- Preserved the existing validation that requires one complete dialogue segment per hop.
- Removed the fallback path that could cut through a sentence just to satisfy word budgets.

Relevant areas:

- [app/features/videos/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py#L652)
- [app/features/videos/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py#L769)
- [app/features/videos/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py#L793)

### 2. Vertex extension hops now keep the requested Lite model

Changed in [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L1764)

- Read `requested_model` from post metadata.
- Passed that model into `submit_video_extension(...)`.
- Persisted `requested_model` and `provider_model` back into metadata after each hop.

Relevant areas:

- [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L1764)
- [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L1801)
- [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L1864)

### 3. Tests now lock the behavior

Changed in:

- [tests/test_video_duration_routing.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_video_duration_routing.py#L327)
- [tests/test_video_poller_extension_chain.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_video_poller_extension_chain.py#L471)

New coverage includes:

- 32s live scripts stay on sentence boundaries.
- 16s and 32s packing still produces full dialogue units.
- Vertex extension hops receive `model="veo-3.1-lite-generate-001"` when the batch requests Lite.

## Exact Simulation Results

I simulated the exact base + three extension prompts for all five posts in the batch before running the live provider jobs.

Result:

- 5 posts
- 5 base prompts
- 15 extension prompts
- 0 segment leakage
- 0 mid-sentence extension starts
- 100 percent of simulated extension hops routed to `veo-3.1-lite-generate-001`

The simulation script also checked that each extension prompt only contained its own segment and did not leak the other 32s segments into the hop text.

### Simulated segment boundaries

Post `a57947cc-bf4f-4991-939e-ba671dc0337b`

```text
1. Deutschland 2026. Und du suchst eine wirklich altersgerechte Wohnung.
2. Langfristige Planung ist dabei entscheidend, besonders für Mehrgenerationen und Pflegearrangements.
3. Der Zuschuss 455 B hilft zwar mit bis zu 2.500 Euro, deckt aber oft nur einen Bruchteil der Kosten ab.
4. Rechtliche Aspekte wie Eigentumsverhältnisse, Regelungen für den Todesfall und die Kostenaufteilung sollten vertraglich klar geregelt werden.
```

Post `5728f5ad-85f8-4799-9852-2fa1a1b9c848`

```text
1. Seniorengerecht klingt gut, aber ist rechtlich wertlos! Viele Begriffe auf Immobilienportalen wie "behindertenfreundlich" sind ungeschützt und garantieren keine echte Barrierefreiheit.
2. Nur "rollstuhlgerecht" nach DIN 18040-2 sichert dir wirklich ausreichend Bewegungsflächen und Türbreiten.
3. Achte auf die Kennzeichnung "R", um böse Überraschungen zu vermeiden.
4. Stell Förderanträge bei der Pflegekasse oder KfW immer, bevor du Maßnahmen beginnst.
```

Post `4944d7ee-1927-4c95-8548-193a15a3e254`

```text
1. Du kennst dieses Gefühl, wenn du endlich eine barrierefreie Toilette findest, die auch WIRKLICH nutzbar ist?
2. Manchmal fühlt sich das an wie ein kleiner Lottogewinn.
3. Ich wünschte, es gäbe eine einfache Karte für solche Geheimtipps. Deswegen ist der Austausch mit euch so Gold wert.
4. Wir lernen so viel voneinander und machen den Alltag leichter. Eure Erfahrungen helfen wirklich.
```

Post `1e950c92-64ad-4027-8cd8-3a3ce95f55fa`

```text
1. Alle reden über Sport und Muskeln, wenn es um Energie im Rollstuhl geht.
2. Aber niemand spricht darüber, wie wichtig die richtige Sitzposition wirklich ist.
3. Ich dachte früher, das sei nur Komfort.
4. Dabei entlastet eine optimale Haltung unglaublich und spart dir Kraft.
```

Post `f618d80b-beb2-4dc1-aedf-70fffd7f94ec`

```text
1. Spontane Freizeit braucht im Rollstuhl oft mehr Planung als man von außen sieht.
2. Mit einer klaren Routine bleibst du im Alltag trotzdem deutlich entspannter.
3. Genau solche Kleinigkeiten entscheiden oft darüber, ob sich ein Weg leicht oder unnötig anstrengend anfühlt.
4. Darüber wird selten gesprochen, obwohl es im Rollstuhl-Alltag ständig wieder passiert.
```

## Live Verification

### Submission

The live rerun was executed against the same batch with:

- Provider: `vertex_ai`
- Requested model: `veo-3.1-lite-generate-001`
- Requested seconds: `32`
- Route: `8 + 7 + 7 + 7`

Observed results:

- 5/5 posts accepted the base operation on Lite
- 5/5 posts accepted all three extension hops on Lite
- 5/5 posts reached `caption_completed`

### Prompt audit confirmation

Post-run audit count:

- 20 total prompt audit rows
- 5 base prompts
- 15 extension prompts

Per-post result:

- 1 base prompt
- 3 extension prompts
- 4 total operations
- `requested_model = veo-3.1-lite-generate-001`
- `provider_model = veo-3.1-lite-generate-001`

### Final live video URLs

1. [a57947cc-bf4f-4991-939e-ba671dc0337b](https://pub-7036b4dec03b49e5bacaab577befbbbf.r2.dev/flow-forge/videos/20260514T214741Z_captioned_20260514T214741Z_a57947cc-bf4f-4991-939e-ba671dc0337b.mp4)
2. [5728f5ad-85f8-4799-9852-2fa1a1b9c848](https://pub-7036b4dec03b49e5bacaab577befbbbf.r2.dev/flow-forge/videos/20260514T214632Z_captioned_20260514T214632Z_5728f5ad-85f8-4799-9852-2fa1a1b9c848.mp4)
3. [4944d7ee-1927-4c95-8548-193a15a3e254](https://pub-7036b4dec03b49e5bacaab577befbbbf.r2.dev/flow-forge/videos/20260514T214716Z_captioned_20260514T214716Z_4944d7ee-1927-4c95-8548-193a15a3e254.mp4)
4. [1e950c92-64ad-4027-8cd8-3a3ce95f55fa](https://pub-7036b4dec03b49e5bacaab577befbbbf.r2.dev/flow-forge/videos/20260514T214653Z_captioned_20260514T214653Z_1e950c92-64ad-4027-8cd8-3a3ce95f55fa.mp4)
5. [f618d80b-beb2-4dc1-aedf-70fffd7f94ec](https://pub-7036b4dec03b49e5bacaab577befbbbf.r2.dev/flow-forge/videos/20260514T214814Z_captioned_20260514T214814Z_f618d80b-beb2-4dc1-aedf-70fffd7f94ec.mp4)

### Local captioned artifacts

The captioned MP4s were also downloaded locally for inspection under:

- `/tmp/aiugc_veo3_lite_cc027a62_captioned`

## Verification Commands

Targeted tests:

```bash
.venv/bin/pytest -q \
  tests/test_video_duration_routing.py::test_build_veo_extended_base_prompt_rejects_under_segmented_32s_chain \
  tests/test_video_duration_routing.py::test_build_veo_extended_base_prompt_packs_five_sentences_into_four_segments_for_efficient_32s \
  tests/test_video_duration_routing.py::test_build_veo_extended_base_prompt_keeps_live_32s_segments_on_sentence_boundaries \
  tests/test_video_duration_routing.py::test_build_veo_extended_base_prompt_packs_to_two_segments_for_efficient_16s \
  tests/test_video_duration_routing.py::test_build_veo_extended_base_prompt_uses_time_budgeted_packing_for_efficient_32s \
  tests/test_video_poller_extension_chain.py
```

Result:

- `38 passed`

Additional live assertions:

- Simulation script verified the exact prompt text for all 5 posts.
- Prompt audit verification confirmed 20 audit rows.
- Final batch state verification confirmed all 5 posts were `caption_completed`.

## Notes For The Next Model

- Do not reintroduce any word-level fallback in the 32s packing path.
- Keep Vertex extension hops explicitly on the requested model. The extension path should not silently fall back to the adapter default.
- If you want the public `lippelift.xyz` surface to reflect this fix, deploy the patched checkout. The live rerun here exercised the real provider and persistence layers from the local code, not the hosted site.
- If you need to inspect the videos again, use the R2 URLs above or the local `/tmp/aiugc_veo3_lite_cc027a62_captioned` directory.
- Avoid `urllib.request` for the R2 caption URLs; direct `curl` or the app storage client worked reliably, while a plain Python `urlopen` hit a `403` on one attempt during artifact download.

