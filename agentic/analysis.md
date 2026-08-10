# Debug Note

Task signal: semantic multi-take video cut regression.

Route: GENERAL + EYE Debug.

Defect: delivered 32s Semantic UGC video shows undesirable camera/face motion around cuts; older reference appears clean.

Affected batch/post: `4c758a6b-2110-41db-b685-51865fa8584f` / `573ca7cb-c052-57bb-8d3b-cbac30169504`.

Reference delivery: semantic run/post identifier `44ec6ade-2f33-4f77-8ffb-eb31f6053981`.

Evidence plan:
- obtain exact persisted take prompts, negative prompts, seeds, request metadata, raw take URLs, and pipeline manifest for affected and reference runs;
- compare delivered and source frames around every cut;
- distinguish provider terminal motion from composer crops/reframes and frame borrowing;
- inspect git history for prompt/composition contract changes between the two run timestamps;
- identify one causal chain and define a regression-proof correction with pass/fail checks.

Root cause:
- the August 6 operator-review fallback discarded all validated trim windows after one acoustic seam missed its post-word guard by 10 ms, then concatenated four complete 8s provider takes;
- this exposed 210–290 ms per take that transcript/tail QA had excluded, including a face dissolve and terminal camera push-in;
- the single-frame `0.080` terminal-reset detector missed distributed motion scoring `0.075` and `0.072`, the final take was not source-tail evaluated, and the fallback bypassed delivered visual seam QA;
- the August 3 prompt is stricter than the July reference prompt and is a contributing timing constraint, not the primary regression: requested final word near 6.5s, actual final words landed around 7.46–7.54s.

Reference mechanism: the July delivery retained per-take head/tail trims (tail trims 0.285–1.4s), so unstable provider endings never reached its cuts.

Correction:
- preserve Deepgram windows in operator-review fallback;
- clamp every non-final take to the existing 350 ms visual-tail exclusion;
- use the existing bounded pitch-preserving A/V retime only when an exact delivery target requires it;
- detect terminal resets from a one-frame threshold or a two-frame cumulative threshold;
- run delivered-boundary visual QA on transcript-safe operator-review fallbacks instead of bypassing it.

Validation:
- focused automated suite: 111 passed;
- actual affected raw takes under detector v2: distributed resets now found in takes 1 and 2; final push-in found in take 3;
- actual affected recomposition: 30.75s, within the 30.5–32.5s contract, with tail trims `[0.35, 0.35, 0.35, 0.29]` and no unstable raw terminal frames at the cuts. Delivered-boundary QA correctly preserves one 167 ms frozen-frame advisory at seam 1; removing it would cut through the final spoken word, so it remains explicit operator-review evidence rather than triggering an unsafe trim or silent paid retry.
