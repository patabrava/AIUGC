"""
Lifestyle PROMPT_2 integrity testscript.
Verifies a single-sentence lifestyle script is preserved instead of collapsing to an empty rotation.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.features.topics.agents as topic_agents
from app.features.topics.schemas import DialogScripts


def main() -> None:
    full_script = (
        "Schon mal erlebt, wie ein kleiner Trick mit dem Rollstuhl deinen ganzen Ausflug entspannter und selbstständiger macht?"
    )

    original_generate_dialog_scripts = topic_agents.generate_dialog_scripts

    def fake_generate_dialog_scripts(topic: str, scripts_required: int = 1, previously_used_hooks=None):
        return DialogScripts(
            problem_agitate_solution=[full_script],
            testimonial=[full_script],
            transformation=[full_script],
            description="Zusätzlicher Kontext für den Lifestyle-Post.",
        )

    topic_agents.generate_dialog_scripts = fake_generate_dialog_scripts
    try:
        generated = topic_agents.generate_lifestyle_topics(count=1, seed=1)
    finally:
        topic_agents.generate_dialog_scripts = original_generate_dialog_scripts

    assert len(generated) == 1, generated
    topic = generated[0]

    assert topic["rotation"] == full_script, topic
    assert topic["cta"] == "entspannter und selbstständiger macht?", topic

    payload = topic_agents.build_lifestyle_seed_payload(topic, topic["dialog_scripts"])
    assert payload["script"] == full_script, payload
    assert payload["dialog_script"] == full_script, payload


if __name__ == "__main__":
    main()
