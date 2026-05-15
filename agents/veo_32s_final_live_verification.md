# Veo 32s Final Live Verification

Date: 2026-05-15

## Scope

- Route kept at `8 + 7 + 7 + 7` as requested.
- Provider: `vertex_ai`
- Model: `veo-3.1-lite-generate-001`
- Batch: `e682ccc9-a094-4d46-8473-1960f43e5868`
- Batch state after run: `S6_QA`
- Target length tier: `32`
- Poller scope: `codex-local-32s.test`
- Shared seed: `2460544400`

The earlier batch `8f80b09e-1e7c-47ae-a4af-d36a5e165478` was discarded as acceptance evidence because a non-local poller acquired one extension lease.

## Posts

### Lifestyle

- Post: `2e7b8367-d2b7-4159-b6ff-9c5be9ccfd08`
- Status: `caption_completed`
- Captioned video: `https://pub-7036b4dec03b49e5bacaab577befbbbf.r2.dev/flow-forge/videos/20260515T002913Z_captioned_20260515T002913Z_2e7b8367-d2b7-4159-b6ff-9c5be9ccfd08.mp4`
- Local MP4: `/tmp/aiugc_veo32_final_e682/lifestyle_2e7b8367-d2b7-4159-b6ff-9c5be9ccfd08_captioned.mp4`
- Contact sheet: `/tmp/aiugc_veo32_final_e682/lifestyle_contact.jpg`
- Segment budgets: `20/16 @ 8s`, `15/14 @ 7s`, `14/14 @ 7s`, `15/12 @ 7s`
- Caption word count: `64`
- ffprobe duration: `28.708333`
- silencedetect: one internal silence, `21.122646-21.473583`, duration `0.350938s`
- Deepgram transcript words: `64`
- Word timing overlaps over 30ms: `0`
- Deepgram first/last word timing: `heute @ 0.080s`, `selbstbestimmter @ 27.755s`
- Visual inspection: same actor and room across sampled frames at approx. `1s`, `8s`, `15s`, `22s`, `27.5s`

Prompt audit rows:

- `0cb60cfb-3502-44a3-826b-21ac5ae8c92d` - `veo_extended_segment`, `32s`, seed `2460544400`, op `343a9cd6-93a7-43e8-9071-80721f94998f`
- `1335ac8d-d66c-425f-bfbc-8f1f4324ca44` - `veo_extension_hop`, `7s`, seed `2460544400`, op `26333a0d-ae8a-4042-a6b7-8b186d354f61`
- `ebc44483-2a05-4170-8bdb-fb87fa869852` - `veo_extension_hop`, `7s`, seed `2460544400`, op `72c620e4-36a2-4a30-aaf5-6d1f0fb426f8`
- `bed8d2e9-0972-4065-b63b-c7f43055ef1b` - `veo_extension_hop`, `7s`, seed `2460544400`, op `c74ae7e5-bb95-4418-af46-fbdf80b5312f`

Prompt checks:

- Four audit rows exist.
- Every row has `negative_prompt`.
- Every row has seed `2460544400`.
- Segment isolation counts: `[1,0,0,0]`, `[0,1,0,0]`, `[0,0,1,0]`, `[0,0,0,1]`.
- `She says` count: `0`.

### Value

- Post: `e11aacf3-a8be-4f5a-b51b-87ed4593b0ae`
- Status: `caption_completed`
- Captioned video: `https://pub-7036b4dec03b49e5bacaab577befbbbf.r2.dev/flow-forge/videos/20260515T002837Z_captioned_20260515T002837Z_e11aacf3-a8be-4f5a-b51b-87ed4593b0ae.mp4`
- Local MP4: `/tmp/aiugc_veo32_final_e682/value_e11aacf3-a8be-4f5a-b51b-87ed4593b0ae_captioned.mp4`
- Contact sheet: `/tmp/aiugc_veo32_final_e682/value_contact.jpg`
- Segment budgets: `18/16 @ 8s`, `14/14 @ 7s`, `17/14 @ 7s`, `17/12 @ 7s`
- Caption word count: `66`
- ffprobe duration: `28.708333`
- silencedetect: two internal silences, `6.932896-7.455458`, duration `0.522562s`; `7.650083-8.015479`, duration `0.365396s`
- Deepgram transcript words: `66`
- Word timing overlaps over 30ms: `0`
- Deepgram first/last word timing: `wenn @ 0.080s`, `klingt @ 27.865s`
- Visual inspection: same actor and room across sampled frames at approx. `1s`, `8s`, `15s`, `22s`, `27.5s`

Prompt audit rows:

- `eb557495-e54c-4589-8c64-29663090fd52` - `veo_extended_segment`, `32s`, seed `2460544400`, op `cb22ceb5-8267-46c8-b702-49aca69eecf5`
- `27762dde-763f-46bd-b5ab-c90ffc1c77e1` - `veo_extension_hop`, `7s`, seed `2460544400`, op `609f8390-c163-40a9-b5a3-651ed2cd3f16`
- `8fb3e9bd-69d6-4c7f-80d3-59864f871936` - `veo_extension_hop`, `7s`, seed `2460544400`, op `65af2aa2-bd15-47d1-9bf4-be0a5d985845`
- `e49c4f09-21f6-4473-81f3-726d2da5afde` - `veo_extension_hop`, `7s`, seed `2460544400`, op `00054216-1490-4274-b0e4-2d616fa51a01`

Prompt checks:

- Four audit rows exist.
- Every row has `negative_prompt`.
- Every row has seed `2460544400`.
- Segment isolation counts: `[1,0,0,0]`, `[0,1,0,0]`, `[0,0,1,0]`, `[0,0,0,1]`.
- `She says` count: `0`.

## Verdict

Pass for the requested acceptance criteria:

- Character consistency: passed by sampled-frame inspection.
- Audio consistency: passed by single-speaker Deepgram transcript and no music/background voices detected audibly in review.
- No script overlap: passed by prompt audit segment isolation and one segment per operation.
- No overlapping words: passed by Deepgram word timings, `0` overlaps over 30ms on both captioned MP4s.
- Captions: passed by full-resolution seam frame inspection; one caption word visible at a time.
