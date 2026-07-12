# Duration-Driven Veo UGC Design

## Goal

Replace the fixed four-take 16-second pilot with a duration-driven Veo 3.1 composition system that uses the fewest independent generations allowed by Veo's eight-second shot ceiling. Prove the change with a new two-shot version of the existing approved 16-second pilot and a deterministic seven-shot 50-second planning test.

## Product contract

- Requested duration and generated-shot count are separate concepts.
- A Veo generation may be 4, 6, or 8 seconds and never more than 8 seconds.
- The planner selects the minimum viable number of generations whose speech capacity fits the script.
- Semantic boundaries outrank equal-duration splitting. No word or phrase may be split merely to hit a time target.
- Additional AIUGC rhythm may come from picture-only edits; it must not create a new audio performance boundary.
- Existing two-actor-reference plus canonical-location preparation remains the identity authority. The approved master produced from that package remains the first-frame truth for this pilot.
- Native Veo voice remains the audio source. No replacement TTS is introduced.

## Planning model

The planner estimates natural spoken duration from the existing word-rate contract, then searches for the smallest shot count that can partition the script into semantic beats with no beat exceeding the safe spoken capacity of one eight-second generation. Strong punctuation is preferred, then comma and conjunction boundaries, then neutral word boundaries as a last resort for long scripts.

Provider duration is selected independently for each beat from 4, 6, or 8 seconds. The final short beat may use a smaller bucket. A 30-word script should normally compile to two 8-second requests. A roughly 50-second spoken script should compile to approximately seven 8-second requests, subject to semantic boundaries and actual word count.

## Shot and reference model

The deterministic shot deck accepts the planned shot count. It cycles restrained master-derived framings rather than requiring exactly four variants. Each generated shot is still independently anchored to the approved master; generated outputs never become the identity authority for later shots.

The first live comparison uses two variants: original and restrained center crop. The visual cut remains intentional while actor, wardrobe and location stay locked.

## Composition and QA

All gates become cardinality-independent:

- transcript QA runs for every planned take;
- voice QA accepts two or more ordered full-take clips and identifies outliers within the actual range;
- visual contact sheets and visual QA accept the planned take count;
- acoustic analysis creates one seam plan and one qualitative seam clip for every adjacent pair;
- composition accepts arbitrary take counts and preserves hard picture cuts with native-audio micro-crossfades;
- upload remains content-addressed and requires every enabled gate.

The final transcript must have WER 0.0 against the approved script. Every generated shot must retain the same actor and environment. A failed take is regenerated in isolation.

## X-reference comparison

The cached reference video is the editorial baseline. Automated comparison records:

- duration;
- true visual cut timestamps;
- visible shot durations;
- cuts per second and average seconds per cut;
- final transcript and seam gaps;
- acoustic seam verdict;
- visual/voice/media QA;
- local and remote checksums.

The new 16-second render passes the editorial-density comparison when it contains exactly one true generation cut, no accidental extra scene cut, and an average visible shot duration materially closer to the reference's approximately nine-second rhythm than the current four-take result.

## Compatibility and migration

Existing manifests remain resumable because their persisted beat and take arrays remain authoritative. New manifests record a planning profile and requested duration. Fixed `[4, 6, 6, 4]` validation is removed from new-plan creation and paid-request validation; the content-addressed request-contract hash continues to prevent mutation after approval.

The existing four-take preview remains archived as the comparison control. Generated media and credentials remain untracked.

## End-to-end acceptance

1. Unit tests prove two-shot 16-second planning and seven-shot 50-second planning.
2. The focused semantic-pilot suite passes for variable cardinalities.
3. A new manifest submits exactly two paid eight-second Veo operations from the approved inputs.
4. Both takes pass transcript, voice and visual identity gates.
5. The final video has one hard picture cut, native audio, captions, WER 0.0 and passed acoustic/media QA.
6. A comparison report shows the new cut density is closer to the X reference than the four-take control.
7. The distinct uploaded preview matches the local artifact byte for byte.
