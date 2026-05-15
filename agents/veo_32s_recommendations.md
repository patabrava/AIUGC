# VEO 32s Recommendations

Context: character drift is the remaining issue after the 32s sound and dialogue fixes. The goal now is to reduce identity drift across extension hops without reintroducing prompt bloat.

## Recommended Options

1. Canonical reference image anchor
   - Best next step for character consistency.
   - Lock one subject image for the character and reuse it as the visual anchor for generation.
   - This reduces how much identity has to be reconstructed from text on every hop.

2. First/last-frame continuation
   - Use the end frame of the previous clip as the next anchor instead of relying on long text carry-over.
   - Stronger than pure prompt extension when drift compounds across hops.

3. Keep the character prompt minimal and invariant
   - Preserve one boring, canonical identity block.
   - Remove decorative detail, extra descriptors, and narrative prose from the identity section.
   - Treat any prompt expansion as a regression risk unless it is proven stable.

4. Hold seed and model constant
   - Keep the same seed and model variant across the chain.
   - Vary one thing at a time so drift can be attributed to a single cause.

5. Assume prompt rewriting stays on
   - Do not try to out-prompt the rewriter with extra detail.
   - Keep the prompt contract rigid and minimal upstream.

6. Decouple voice if it drifts again
   - If audio consistency regresses, generate voice once and sync it after render rather than asking each hop to regenerate speech independently.

## Order Of Attack

1. Add the canonical reference image anchor.
2. Test first/last-frame re-anchoring for 32s.
3. Tighten the character block further.
4. Keep seed/model fixed during the experiment.

Sources:
- https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/extend-a-veo-video?hl=de
- https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/use-reference-images-to-guide-video-generation
- https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/best-practice
- https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide
- https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/turn-the-prompt-rewriter-off
