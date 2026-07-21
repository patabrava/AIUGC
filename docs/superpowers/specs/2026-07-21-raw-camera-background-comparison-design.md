# Raw Camera Background Comparison Design

Date: 2026-07-21
Status: Approved for implementation

## Goal

Evaluate whether the existing long Raw Camera Casting Realism prompt produces more believable actor-free background reference pictures than the current Reality-First prompt.

## Test Scope

Generate one new image for each of three representative canonical scenes:

- `home_living_room_advice_a`: neutral indoor scene;
- `bathroom_accessibility_a`: detailed accessibility-focused indoor scene;
- `car_transfer_residential_a`: outdoor residential scene.

The existing production image is the control. The newly generated Raw Camera image is the treatment. Existing canonical assets, database records, storage objects, and production routing remain unchanged.

## Prompt Architecture

For each scene, pass the existing scene bible's identity, generation anchor, composition, lighting, and scene-specific rejectors to the long system instruction in `app/features/shot_frames/raw_camera_casting_system_prompt.txt`. The prompt writer must produce a finished actor-free, vertical 9:16 background-generation prompt.

The brief must explicitly require:

- no people, body parts, or wheelchairs;
- a physically plausible, stageable environment;
- the exact scene anchors and relative layout from the scene bible;
- ordinary camera-file realism with natural material imperfections and lighting;
- no text, logos, UI, beauty polish, fake HDR, glow, or stylization;
- environment-only composition without subject- or hand-visibility instructions.

Send the finished prompt to the existing Gemini image adapter with the same `9:16` aspect ratio used by the current canonical scenes. Use the current Raw Camera shot-frame image model unless provider compatibility requires the canonical-scene model.

## Implementation Boundary

Add a standalone evaluation script and small testable prompt-building functions. The script will:

1. load the three current canonical assets from Supabase;
2. download each control image;
3. produce a Raw Camera prompt through the existing text-generation adapter;
4. generate one treatment image through the existing image-generation adapter;
5. write control and treatment images, prompts, hashes, provider metadata, and errors into a timestamped directory under `output/background-reference-comparison/`;
6. create a side-by-side comparison image for each scene;
7. create an HTML index showing all three comparisons at full portrait aspect ratio.

Generation failures are recorded per scene and do not overwrite successful results from other scenes. The script exits non-zero if any requested comparison fails.

## Visual Review

The HTML index and browser visual companion will label each pair consistently:

- left: `Current · Reality-First`;
- right: `Test · Raw Camera Casting Realism`.

Review criteria are physical realism, natural materials, believable lighting, absence of AI polish, scene-layout fidelity, visual usefulness behind the actor, and absence of people or body parts.

## Validation

Automated tests will verify that the Raw Camera prompt-writer brief is actor-free, excludes the contradictory subject/hands composition, preserves scene anchors, and uses the long prompt file. Tests will also verify deterministic HTML/manifest construction without making provider calls.

The live validation is complete only when all three new images exist, all three side-by-side comparisons render correctly, the HTML index opens in the browser, and the manifest confirms that no production asset was updated.
