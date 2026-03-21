# Topics Hub Contract

## Goal
Create a read-first topics hub page that lets batch owner/editors browse existing topics, inspect their script variants, review research-run history, and launch a new deep research run for one topic on demand.

## Primary User / Actor
Batch owner/editor working inside the FLOW-FORGE web app while managing topic inventory and deciding whether a topic needs fresh research.

## Inputs
- Existing topic registry rows
- Existing topic script rows
- Existing topic research run rows
- A selected topic or lane for on-demand deep research
- Optional filters such as post type, length tier, cluster, lane family, and run status

## Outputs / Deliverables
- A `/topics` hub page with list and detail views
- A topic table or card list showing topic metadata and script availability
- A script drill-in panel showing variants per topic and length tier
- A research-run history panel showing status, timestamps, and result or error summary
- An action to launch a new research run for one topic

## Core Pipeline
1. Load the topics hub page with topics, scripts, and recent research runs.
2. Apply filters to narrow the visible set without leaving the hub.
3. Open a topic to inspect its scripts, source metadata, and stored research context.
4. Launch a new deep research run for the selected topic when requested.
5. Refresh or poll run state until the run completes or fails.
6. Update the visible topic/script inventory from the stored backend records.

## Data / Evidence Contracts
- The hub must render only stored backend data for topics, scripts, and runs.
- Any research launcher action must persist a durable run record before the request is treated as started.
- Run status must be observable after refresh; in-memory-only state is not sufficient for the user-facing tracker.
- Research claims shown in the UI must come from the stored research payload, sources, or run result summary, not from ad hoc client-side inference.
- If a topic is missing script or research data, the hub must show that absence explicitly rather than fabricating a filled state.

## Constraints
- Use the existing FastAPI, Jinja, HTMX, and Alpine stack.
- No new framework by default.
- Prefer a small page-local slice over a shared global abstraction.
- Keep the page read-first; launching research is secondary to browsing and inspection.
- Target a locality budget of roughly 6-8 files, about 120-260 LOC per file, with 0 new dependencies.
- Keep backend semantics stable; add only the minimal read/launch endpoints needed to support the hub.
- Preserve the current batch workflow and do not route this page through batch detail.

## Non-Goals / Backlog
- Do not fold this into batch detail.
- Do not build a full content management system.
- Do not add topic editing, deletion, or bulk mutation in the first pass.
- Do not add new AI generation logic in the frontend.
- Do not require realtime sockets unless polling proves insufficient.
- Do not redesign the visual system beyond what the existing app already uses.

## Definition of Done
- The app has a dedicated `/topics` hub page.
- Users can browse topics and inspect associated scripts from the hub.
- Users can see research-run history for topics.
- Users can launch a new deep research run for one topic from the hub.
- Run state remains visible after refresh.
- The implementation stays within the agreed locality budget and uses no unnecessary dependencies.
