from types import SimpleNamespace

from app.features.topics.pipeline import (
    RawResearchArtifact,
    run_stage1_raw_research,
    run_stage2_normalization,
    run_stage3_script_generation,
)


class FakeLLM:
    def __init__(self):
        self.deep_research = []
        self.json_calls = []
        self.text_calls = []

    def generate_gemini_deep_research(self, prompt, system_prompt=None, **kwargs):
        self.deep_research.append((prompt, system_prompt, kwargs))
        return "raw research content"


def test_pipeline_stage_functions_exist(monkeypatch):
    fake_llm = FakeLLM()
    monkeypatch.setattr("app.features.topics.pipeline.get_llm_client", lambda: fake_llm)
    monkeypatch.setattr(
        "app.features.topics.pipeline.normalize_topic_research_dossier",
        lambda **kwargs: SimpleNamespace(topic="Erklärtes Thema"),
    )
    monkeypatch.setattr(
        "app.features.topics.pipeline.generate_dialog_scripts",
        lambda **kwargs: SimpleNamespace(problem_agitate_solution=["Hook."], testimonial=["Hook."], transformation=["Hook."], description="Beschreibung."),
    )

    artifact = run_stage1_raw_research(seed_topic="Pflegegrad", post_type="value", target_length_tier=8)
    assert isinstance(artifact, RawResearchArtifact)
    assert artifact.raw_response == "raw research content"

    dossier = run_stage2_normalization(artifact)
    assert dossier.topic == "Erklärtes Thema"

    scripts = run_stage3_script_generation(topic="Pflegegrad", scripts_required=1, dossier=dossier)
    assert scripts.problem_agitate_solution == ["Hook."]
