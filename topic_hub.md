## Topics Hub Implementation Overview

### Key files created/updated
- `app/features/topics/hub.py`: orchestrates the hub payload, including filter parsing, topic listing, script grouping by usage state, and the helper that synthesizes script-bank rows when stored scripts are missing.
- `app/features/topics/handlers.py`: ensures the `/topics/runs` launch endpoint preserves UI filters (like `script_usage`) in the post-redirect-get cycle so the hub state remains consistent after launching research.
- `templates/topics/hub.html`: renders the split pane layout (script inventory on the left, selected topic inspector on the right), wires up the usage toggle, grouped script cards, launch form, and run list.
- `templates/topics/partials/run_card.html`: reused by the hub to show the active/completed run list; no new logic added but referenced for display consistency.
- `tests/test_topics_hub.py`: verifies the script grouping/filtering logic and exercise the new payload helpers.

### Interactions
- `hub.py` feeds the template by exposing `filters`, `topics`, `selected_topic`, grouped scripts, and runs; it relies on registry data (`get_all_topics_from_registry`, `get_topic_scripts_for_registry`) and script fallback synthesis from the same module.
- `handlers.py` posts to `create_topic_research_run` and redirects back to `/topics`, passing the current filters (`script_usage`, `topic_id`, etc.) so the hub view stays in sync.
- `hub.html` consumes the payload keys to power the UI: the left column uses `topics`, `filters`, and `selected_topic` to highlight the current selection; the right column uses `selected_topic`, `selected_script_groups`, and `runs` plus the `script_usage` filter to show the grouped scripts and research controls; it also reuses `run_card.html` for each run.
- `tests/test_topics_hub.py` exercises `hub.build_topic_hub_payload`, ensuring script grouping respects `script_usage` toggles before the template renders.

The new `topic_hub.md` captures the topology of these files so future work can trace how filtering, script grouping, launch redirects, and template rendering coordinate across the feature.
