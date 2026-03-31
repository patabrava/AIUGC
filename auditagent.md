# German Nativeness Audit Agent

## Goal
- Add a read-only audit agent that reviews persisted topic scripts for:
  - native-sounding German
  - hook compliance
  - prompt compliance
  - TikTok engagement potential
  - leakage from research notes, citations, metadata, or markdown residue

## Role
- The agent is a post-persistence quality gate.
- It does not generate scripts.
- It does not rewrite scripts inline.
- It returns structured findings only.

## Why It Exists
- Local validators are good at structural checks.
- They are not enough to judge:
  - natural German phrasing
  - audience grip
  - whether the hook feels like something a native speaker would actually say
- This agent adds a second layer of quality review without slowing down generation itself.

## Placement In The Pipeline
1. A script is generated.
2. Deterministic validation runs first.
3. The German nativeness audit runs on surviving candidates.
4. Scripts that fail are flagged for repair or rejection.
5. Audit results are stored for trend analysis.

## What It Should Check
- German nativeness
  - Does it sound like native spoken German?
  - Is the phrasing natural, direct, and fluid?
  - Does it avoid translation artifacts?

- Hook quality
  - Does the opening line pull attention fast?
  - Is there a clear reason to keep listening?
  - Does the hook fit TikTok pacing?

- Prompt compliance
  - Does it follow the initial prompt’s intent?
  - Does it respect the tier length?
  - Does it stay inside the topic scope?

- Integrity
  - No research labels
  - No citation fragments
  - No markdown residue
  - No truncated endings
  - No metadata bleeding into the final script

## Output Contract
- The agent should return a structured result with:
  - `status`: `pass`, `needs_repair`, `reject`, or `manual_review`
  - `reasons`: list of short findings
  - `severity`: optional numeric or tiered score
  - `notes`: optional human-readable explanation
  - `script_id`: the persisted script being reviewed

## Design Rules
- Keep it read-only.
- Run it after the deterministic validator.
- Use it to measure quality drift over time.
- Keep it separate from the generation path so bad output never becomes the source of truth.
- Prefer local checks first so the model is only used on plausible candidates.

## Recommended Implementation Shape
- One audit worker or service that:
  - reads pending or fresh `topic_scripts`
  - evaluates each script against a fixed checklist
  - stores the audit result
  - optionally queues repair when `needs_repair`

## Suggested Follow-Up Questions
- Should the audit agent run synchronously during publish, or asynchronously after persistence?
- Should `needs_repair` trigger a deterministic local repair pass before human review?
- Should audit results be stored on `topic_scripts`, in a separate audit table, or both?
