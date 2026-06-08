# Canonical Scene Consistency Report

Date: 2026-06-08

## Scope

- Applied live schema for `canonical_scene_assets`.
- Generated canonical scene anchors on Vertex Gemini 3 Pro Image through the live project.
- Ran 6 live 8-second Vertex Veo 3.1 videos using `2 actor anchors + 1 canonical scene anchor`.
- Downloaded all completed MP4 outputs plus start/end frames.

## Canonical Scene Anchors

- `home_living_room_advice_a`
  - asset id: `37f69d7c-481b-4bcb-be09-83350f8f677b`
  - image: `https://pub-7036b4dec03b49e5bacaab577befbbbf.r2.dev/Lippe%20Lift%20Studio/images/canonical-scenes/home_living_room_advice_a/v1.png`
- `bathroom_accessibility_a`
  - asset id: `e4866628-5e12-458f-8325-eb74633f5470`
  - image: `https://pub-7036b4dec03b49e5bacaab577befbbbf.r2.dev/Lippe%20Lift%20Studio/images/canonical-scenes/bathroom_accessibility_a/v1.png`
- `car_transfer_residential_a`
  - asset id: `81bff419-7290-474a-89a0-bd651acea07b`
  - image: `https://pub-7036b4dec03b49e5bacaab577befbbbf.r2.dev/Lippe%20Lift%20Studio/images/canonical-scenes/car_transfer_residential_a/v1.png`

## Live Video Runs

- Living room 1
  - operation: `bc8cf1c6-ec05-41ad-8f31-9fd68f307dfb`
  - file: `output/video-generation-tests/canonical-scene-matrix-20260608/living-room-1.mp4`
- Living room 2
  - operation: `5fee42fa-20c1-43f7-aa3f-1bf368c112ae`
  - file: `output/video-generation-tests/canonical-scene-matrix-20260608/living-room-2.mp4`
- Bathroom 1
  - operation: `8f64786f-935e-4fb1-9083-fb460bbe2ef0`
  - file: `output/video-generation-tests/canonical-scene-matrix-20260608/bathroom-1.mp4`
- Bathroom 2
  - operation: `4a07059f-c5a9-42cf-9b11-7d2059af2346`
  - file: `output/video-generation-tests/canonical-scene-matrix-20260608/bathroom-2.mp4`
- Car transfer 1
  - operation: `a0b22e38-d6a5-48b0-be38-38f04fadb227`
  - file: `output/video-generation-tests/canonical-scene-matrix-20260608/car-transfer-1.mp4`
- Car transfer 2
  - operation: `0c561162-6d0e-431b-88d7-0d1fa42456cc`
  - file: `output/video-generation-tests/canonical-scene-matrix-20260608/car-transfer-2.mp4`

## Evidence Bundle

- run state: `output/video-generation-tests/canonical-scene-matrix-20260608/state.json`
- start-frame grid: `output/video-generation-tests/canonical-scene-matrix-20260608/frame-start-grid.jpg`
- end-frame grid: `output/video-generation-tests/canonical-scene-matrix-20260608/frame-end-grid.jpg`

## Observations

- Character consistency: good across all 6 runs. Face shape, hair, and age read as the same actor.
- Scene consistency: good within each scenario pair. The living room, bathroom, and curbside car layout stayed stable across prompt variation.
- Residual issue: wardrobe color still drifts between some runs because the current 3-image contract locks actor identity and scene well, but does not yet hard-lock clothing.
