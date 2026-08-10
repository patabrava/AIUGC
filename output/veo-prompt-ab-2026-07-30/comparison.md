# VEO 3.1 old-versus-new-versus-final prompt comparison

## Controlled setup

- Model: `veo-3.1-generate-001`
- Seed: `240713`
- Duration: 8 seconds
- Format: 9:16, 720p, 24 fps
- Audio: enabled
- Sample count: 1 per prompt
- References: identical two actor anchors plus one canonical room anchor, in identical order
- Dialogue: identical German sentence

The original first-frame wording was changed only where necessary to address the supplied three reference images. This keeps the experiment valid for the requested reference-image workflow; Vertex does not combine a first-frame input with three asset references in one request.

## Result

| Criterion | Old prompt | Intermediate prompt | Final prompt | Better |
|---|---|---|---|---|
| Actor identity | Strong, slightly three-quarter/side-biased | Strong, frontal and close to the primary identity anchor | Strong, frontal, with expressive facial motion | Intermediate/final |
| Face readability | Subject is smaller and left-weighted | Face is largest and centered | Centered, but framed wider | Intermediate |
| Requested talking-head framing | Too wide | Closest to the requested medium close-up | Wider than requested; includes lap and most of chair | Intermediate |
| Natural facial performance | Restrained | Restrained, slightly fixed | Best micro-expression and speech-coupled movement | Final |
| Wheelchair visibility | Excellent and mechanically most coherent | Adequate; armrests and wheel edges visible | Excellent visibility, but circular cushion/lap geometry is deformed | Old |
| Canonical room visibility | Excellent | Correct room identity, tighter crop | Excellent | Old/final |
| Camera stability | Pass | Pass until terminal frame | Pass | Old/final |
| Continuous ending | Pass | Scene-change detector flags a blur/reframe at 7.958 s | Pass; no detected internal or terminal cut | Final |
| Spoken dialogue | Exact transcript, 18/18 words | Exact transcript, 18/18 words | Exact transcript, 18/18 words | Tie |
| Final-word timing | “Bewegungen” ends at 7.46 s | “Bewegungen” ends at 7.46 s | “Bewegungen” ends at 7.46 s | Tie |
| Quiet ending | Approximately 0.32 s detected silence | Approximately 0.48 s detected silence | Approximately 0.47 s detected silence | Intermediate/final |
| On-screen text | None observed | None observed | None observed | Tie |

## Verdict

The final prompt is the strongest motion and ending contract, but the intermediate prompt retains the best talking-head composition. The best production prompt is therefore the final prompt with its wheelchair-presence negatives reduced: remove `wheelchair fully obscured`, `missing rear wheel or hand rim`, and similar coverage-forcing phrases, then retain only identity/deformation exclusions plus one positive request for a visible armrest and a small portion of a hand rim.

The old prompt's main advantage remains mechanically coherent wheelchair and environmental coverage. The intermediate prompt wins framing but has a last-frame defect. The final prompt wins naturalism and terminal continuity but demonstrates that an extensive negative prompt can push composition and object geometry in the wrong direction.
