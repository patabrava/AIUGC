from __future__ import annotations

from types import SimpleNamespace

from app.features.blog import blog_runtime
from app.features.blog.schemas import build_blog_content_from_llm, render_body_html
from app.features.topics import prompts, seo_catalog


def _settings(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(seo_topic_catalog_enabled=enabled)


def _blog_payload(*, with_links: bool = False):
    first_paragraph = "Treppenlift Kosten hängen von Treppenform, Ausstattung und Montage ab."
    if with_links:
        first_paragraph += " [[LINK:funding|Fördermöglichkeiten für Treppenlifte]] helfen bei der Planung."
    return {
        "name": "Treppenlift Kosten: Preise richtig einordnen",
        "slug": "treppenlift-kosten-preise",
        "merksatz": "Treppenlift Kosten lassen sich erst nach einer Bedarfsaufnahme belastbar einordnen.",
        "tipp": "Vergleichen Sie Leistungsumfang und Einbausituation gemeinsam.",
        "summary_bullets": ["Treppenform beeinflusst den Preis.", "Ausstattung verändert den Aufwand.", "Förderung kann relevant sein."],
        "intro_heading": "Treppenlift Kosten beginnen mit der Einbausituation",
        "introduction_paragraphs": [first_paragraph],
        "sections": [
            {"heading": "Welche Faktoren den Preis verändern", "paragraphs": ["Kurven, Etagen und Haltestellen beeinflussen die Planung."], "bullets": []},
            {"heading": "Wie Angebote vergleichbar werden", "paragraphs": ["Ein vollständiger Leistungsumfang verhindert falsche Vergleiche."], "bullets": []},
            {"heading": "Welche Förderung geprüft werden kann", "paragraphs": ["Förderwege hängen von der individuellen Situation ab."], "bullets": []},
        ],
        "conclusion_heading": "Treppenlift Kosten nachvollziehbar entscheiden",
        "conclusion_paragraphs": ["Eine Beratung ordnet die konkrete Einbausituation ein."],
        "preview_text": "Treppenlift Kosten verständlich erklärt: Diese Faktoren beeinflussen Planung und Angebot.",
        "meta_title": "Treppenlift Kosten: Preise richtig einordnen",
        "meta_description": "Treppenlift Kosten hängen von Treppe und Ausstattung ab. Erfahren Sie, wie Angebote und mögliche Förderung eingeordnet werden.",
    }


def _seo_brief():
    return {
        "primary_keyword": "treppenlift kosten",
        "secondary_keywords": ["treppenlift zuschuss", "treppenlift gebraucht"],
        "search_intent": "Vergleich/kommerziell",
        "target_audience": "Menschen mit Mobilitätseinschränkungen und Angehörige",
        "internal_links": [
            {"id": "funding", "title": "Fördermöglichkeiten", "url": "https://www.lippelift.de/foerderung"},
        ],
        "cta": "Passende Liftlösung unverbindlich besprechen",
        "avoid_terms": ["garantiert"],
        "source_kind": "catalog",
        "cluster": "Treppenlift-Wissen & Förderung",
        "metrics": {"search_volume": 9900, "provider": "unknown", "metrics_as_of": "unknown"},
    }


def test_workbook_catalog_preserves_every_source_row():
    payload = seo_catalog.load_keyword_catalog()
    assert payload["source"]["full_sheet_rows"] == 131
    assert payload["source"]["curated_sheet_rows"] == 54
    assert payload["source"]["provider"] == "unknown"
    assert payload["source"]["metrics_as_of"] == "unknown"
    assert len(payload["entries"]) == 135
    assert sum(len(entry["curated_rows"]) for entry in payload["entries"]) == 54
    assert len({entry["keyword"].casefold() for entry in payload["entries"]}) == 135


def test_internal_link_catalog_is_verified_host_allowlist():
    payload = seo_catalog.load_internal_link_catalog()
    assert payload["source"]["sitemap_url"] == "https://www.lippelift.de/sitemap.xml"
    assert len(payload["links"]) == 12
    assert all(link["url"].startswith("https://www.lippelift.de/") for link in payload["links"])


def test_flag_off_returns_the_frozen_topic_bank_unchanged(monkeypatch):
    monkeypatch.setattr(prompts, "seo_catalog_enabled", lambda: False)
    frozen = prompts._load_topic_bank_payload()
    assert prompts.get_topic_bank() is frozen
    assert prompts.get_topic_seed_catalog() == frozen["topics"]


def test_flag_on_prepends_search_led_topics(monkeypatch):
    monkeypatch.setattr(prompts, "seo_catalog_enabled", lambda: True)
    topics = prompts.get_topic_seed_catalog()
    assert topics[0] == "Schwerbehindertenausweis: Vorteile und Antrag erklärt"
    assert len(topics) > len(prompts._load_topic_bank_payload()["topics"])
    assert len(topics) == len({prompts._normalize_bank_topic_signature(topic) for topic in topics})
    assert "aktivrollstuhl" not in topics
    assert "Aktivrollstuhl: Worauf es bei Auswahl und Einstellung ankommt" in topics


def test_disabled_selector_never_reads_seo_candidates(monkeypatch):
    from workers import topic_seed_selector

    monkeypatch.setattr(topic_seed_selector, "seo_catalog_enabled", lambda: False)
    monkeypatch.setattr(topic_seed_selector, "get_seo_seed_candidates", lambda: (_ for _ in ()).throw(AssertionError("SEO path called")))
    monkeypatch.setattr(topic_seed_selector, "load_seed_topics_from_yaml", lambda: ["Frozen A", "Frozen B"])
    monkeypatch.setattr(topic_seed_selector, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(topic_seed_selector, "get_researched_topic_texts", lambda: [])
    seeds, source = topic_seed_selector.select_seeds(max_topics=2)
    assert seeds == ["Frozen A", "Frozen B"]
    assert source == "yaml_bank"


def test_enabled_selector_uses_catalog_before_yaml(monkeypatch):
    from workers import topic_seed_selector

    monkeypatch.setattr(topic_seed_selector, "seo_catalog_enabled", lambda: True)
    monkeypatch.setattr(topic_seed_selector, "get_seo_seed_candidates", lambda: ["SEO A", "SEO B", "SEO C"])
    monkeypatch.setattr(topic_seed_selector, "load_seed_topics_from_yaml", lambda: ["Frozen A"])
    monkeypatch.setattr(topic_seed_selector, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(topic_seed_selector, "get_researched_topic_texts", lambda: [])
    seeds, source = topic_seed_selector.select_seeds(max_topics=3)
    assert seeds == ["SEO A", "SEO B", "SEO C"]
    assert source == "seo_catalog"


def test_catalog_brief_uses_neutral_guidance_and_verified_links():
    brief = seo_catalog.build_seo_brief("Was kostet ein Treppenlift? Preise und Einflussfaktoren")
    assert brief["primary_keyword"] == "treppenlift kosten"
    assert brief["source_kind"] == "catalog"
    assert 3 <= len(brief["secondary_keywords"]) <= 6
    assert brief["metrics"]["search_volume"] == 9900
    assert all(link["url"].startswith("https://www.lippelift.de/") for link in brief["internal_links"])
    assert "Sarah" not in str(brief)
    assert "eigener Erfahrung" not in str(brief)


def test_derived_brief_uses_compact_subject_clause_instead_of_full_editorial_title():
    topic = (
        "Technologische Grundlagen Türsensorik - Der Automatik-Tür-Frust "
        "Ursachen, Sensortechnologien und Lösungsstrategien"
    )

    brief = seo_catalog.build_seo_brief(topic)

    assert brief["source_kind"] == "derived"
    assert brief["primary_keyword"] == "technologische grundlagen türsensorik"
    assert len(brief["primary_keyword"]) <= 48


def test_blog_repairs_legacy_derived_brief_with_full_title_as_keyword(monkeypatch):
    topic = (
        "Technologische Grundlagen Türsensorik - Der Automatik-Tür-Frust "
        "Ursachen, Sensortechnologien und Lösungsstrategien"
    )
    monkeypatch.setattr(blog_runtime, "seo_catalog_enabled", lambda: True)

    brief = blog_runtime._resolve_blog_seo_brief(
        {
            "topic": topic,
            "seo_brief": {
                "primary_keyword": (
                    "technologische grundlagen türsensorik - der automatik-tür-frust – "
                    "ursachen, sensortechnologien und lösungsstrategien"
                ),
                "source_kind": "derived",
                "internal_links": [],
            },
        }
    )

    assert brief["primary_keyword"] == "technologische grundlagen türsensorik"


def test_reported_tuersensorik_topic_has_a_satisfiable_blog_contract():
    payload = _blog_payload()
    payload.update(
        {
            "name": "Technologische Grundlagen Türsensorik: Ursachen und Lösungen",
            "slug": "technologische-grundlagen-tuersensorik",
            "intro_heading": "Technologische Grundlagen der Türsensorik",
            "introduction_paragraphs": [
                "Technologische Grundlagen der Türsensorik erklären, warum automatische Türen manchmal stocken."
            ],
            "meta_title": "Technologische Grundlagen Türsensorik | Lippe Lift",
            "meta_description": (
                "Technologische Grundlagen der Türsensorik: Sensorarten, häufige Ursachen und praktische Lösungen."
            ),
        }
    )
    brief = {
        "primary_keyword": "technologische grundlagen türsensorik",
        "source_kind": "derived",
        "internal_links": [],
    }

    assert len(payload["meta_title"]) <= 65
    assert blog_runtime._collect_blog_contract_issues(payload, brief) == []


def test_short_keyword_tokens_do_not_overmatch_unrelated_topics():
    assert seo_catalog.find_keyword_entry("Aktiv und sichtbar bleiben") is None
    broad_match = seo_catalog.find_keyword_entry("Barrierefrei ins Wahlrecht")
    assert broad_match is not None
    assert broad_match["keyword"] == "barrierefreiheit"
    match = seo_catalog.find_keyword_entry("Barrierefreies WC richtig planen")
    assert match is not None
    assert match["keyword"] == "barrierefrei wc"


def test_research_prompt_is_unchanged_when_flag_is_off(monkeypatch):
    monkeypatch.setattr(prompts, "get_enabled_seo_brief", lambda topic: None)
    prompt = prompts.build_topic_research_dossier_prompt(seed_topic="Treppenlift Kosten", post_type="value", target_length_tier=8)
    assert "SEO-DATEN:" not in prompt
    assert "SEO-EINORDNUNG:" not in prompt


def test_research_prompt_includes_structured_seo_without_actor_notes(monkeypatch):
    monkeypatch.setattr(prompts, "get_enabled_seo_brief", lambda topic: _seo_brief())
    prompt = prompts.build_topic_research_dossier_prompt(seed_topic="Treppenlift Kosten", post_type="value", target_length_tier=8)
    assert "SEO-DATEN:" in prompt
    assert "Hauptkeyword: treppenlift kosten" in prompt
    assert "Ich-Erfahrungen" in prompt
    assert "Sarah" not in prompt


def test_actor_script_prompt_does_not_receive_seo_fields():
    dossier = {
        "topic": "Treppenlift Kosten",
        "seed_topic": "Treppenlift Kosten",
        "source_summary": "Belastbare Einordnung aus dem Dossier.",
        "framework_candidates": ["PAL"],
        "facts": ["Die Treppenform beeinflusst den Aufwand."],
        "risk_notes": ["Individuelle Angebote unterscheiden sich."],
        "seo_brief": _seo_brief(),
    }
    lane = {"title": "Kosten richtig einordnen", "lane_family": "ratgeber", "angle": "Einflussfaktoren", "facts": dossier["facts"]}
    prompt = prompts.build_prompt1(post_type="value", desired_topics=1, dossier=dossier, lane_candidate=lane)
    assert "treppenlift zuschuss" not in prompt.lower()
    assert "search_volume" not in prompt
    assert "SEO-DATEN" not in prompt


def test_generated_dossier_snapshots_the_enabled_seo_brief(monkeypatch):
    from app.features.topics import queries, research_runtime
    from app.features.topics.schemas import ResearchDossier

    dossier = ResearchDossier.model_validate(
        {
            "cluster_id": "treppenlift-kosten",
            "topic": "Treppenlift Kosten",
            "anchor_topic": "Treppenlift Kosten",
            "seed_topic": "Treppenlift Kosten",
            "cluster_summary": "Die Einbausituation und Ausstattung bestimmen den individuellen Planungsaufwand.",
            "framework_candidates": ["PAL"],
            "sources": [],
            "source_urls": [],
            "source_summary": "Die Recherche ordnet Einflussfaktoren ein, ohne einen pauschalen Preis zu versprechen.",
            "facts": ["Die Treppenform beeinflusst den Planungsaufwand."],
            "angle_options": ["Einflussfaktoren vor dem Angebotsvergleich prüfen"],
            "risk_notes": ["Pauschale Preisversprechen sind nicht belastbar."],
            "disclaimer": "Keine individuelle Finanzberatung.",
            "lane_candidates": [
                {
                    "lane_key": "kosten-einfluss",
                    "lane_family": "ratgeber",
                    "title": "Treppenlift Kosten einordnen",
                    "angle": "Einflussfaktoren",
                    "priority": 1,
                    "framework_candidates": ["PAL"],
                    "source_summary": "Einflussfaktoren werden vor dem Vergleich eingeordnet.",
                    "facts": ["Die Treppenform beeinflusst den Planungsaufwand."],
                    "risk_notes": ["Keine Pauschalpreise nennen."],
                    "disclaimer": "Keine individuelle Finanzberatung.",
                    "lane_overlap_warnings": [],
                    "suggested_length_tiers": [8],
                }
            ],
        }
    )

    class FakeLlm:
        gemini_topic_timeout_seconds = 300

        def generate_gemini_deep_research(self, **kwargs):
            return "Rohrecherche"

    monkeypatch.setattr(research_runtime, "normalize_topic_research_dossier", lambda **kwargs: dossier)
    monkeypatch.setattr(research_runtime, "get_enabled_seo_brief", lambda topic: _seo_brief())
    monkeypatch.setattr(queries, "create_topic_research_run", lambda **kwargs: {"id": "run-seo"})
    monkeypatch.setattr(queries, "create_topic_research_dossier", lambda **kwargs: {"id": "dossier-seo"})
    monkeypatch.setattr(queries, "update_topic_research_run", lambda *args, **kwargs: {})

    result = research_runtime.generate_topic_research_dossier(
        seed_topic="Treppenlift Kosten",
        post_type="value",
        target_length_tier=8,
        llm_factory=FakeLlm,
    )
    assert result.seo_brief is not None
    assert result.seo_brief.primary_keyword == "treppenlift kosten"
    assert str(result.seo_brief.internal_links[0].url) == "https://www.lippelift.de/foerderung"


def test_blog_uses_original_prompt_file_when_flag_is_off(monkeypatch):
    monkeypatch.setattr(blog_runtime, "seo_catalog_enabled", lambda: False)
    assert blog_runtime._load_prompt_template() == blog_runtime._PROMPT_PATH.read_text(encoding="utf-8")
    prompt = blog_runtime._build_blog_prompt({"topic": "Test", "facts": [], "angle_options": [], "sources": [], "risk_notes": []})
    assert "SEO-KRITERIEN:" not in prompt


def test_blog_seo_prompt_contains_agency_contract_and_link_ids(monkeypatch):
    monkeypatch.setattr(blog_runtime, "seo_catalog_enabled", lambda: True)
    payload = {"topic": "Treppenlift Kosten", "facts": [], "angle_options": [], "sources": [], "risk_notes": [], "seo_brief": _seo_brief()}
    prompt = blog_runtime._build_blog_prompt(payload)
    assert "SEO-KRITERIEN:" in prompt
    assert "Hauptkeyword: treppenlift kosten" in prompt
    assert "[[LINK:funding|natürlicher Ankertext]]" in prompt
    assert "schreibe keine URL selbst" in prompt


def test_blog_contract_validates_keyword_placement_and_link_token():
    issues = blog_runtime._collect_blog_contract_issues(_blog_payload(with_links=True), _seo_brief())
    assert issues == []


def test_blog_keyword_validation_accepts_german_slug_transliteration():
    assert blog_runtime._contains_primary_keyword("tuersensorik-fuer-automatik-tueren", "Türsensorik")


def test_blog_fits_overlong_seo_metadata_deterministically():
    meta_title = "Treppenlift Kosten: " + "gründlich erklärt " * 6
    meta_description = "Treppenlift Kosten: " + "Planung, Förderung und Einbau verständlich erklärt. " * 5

    fitted_title = blog_runtime._fit_seo_metadata(meta_title, 65)
    fitted_description = blog_runtime._fit_seo_metadata(meta_description, 160)

    assert len(fitted_title) <= 65
    assert len(fitted_description) <= 160
    assert fitted_title.startswith("Treppenlift Kosten")
    assert fitted_description.startswith("Treppenlift Kosten")
    assert not fitted_title.endswith((" ", ",", ".", ";", ":", "-"))
    assert not fitted_description.endswith((" ", ",", ".", ";", ":", "-"))


def test_blog_renderer_links_only_allowlisted_ids_and_escapes_html():
    links = _seo_brief()["internal_links"]
    html = render_body_html(
        intro_heading="Einordnung",
        introduction_paragraphs=[
            "Mehr zu [[LINK:funding|passender Förderung]]. <script>alert(1)</script> [[LINK:evil|extern]]"
        ],
        sections=[],
        conclusion_heading="Schluss",
        conclusion_paragraphs=[],
        internal_links=links,
    )
    assert '<a href="https://www.lippelift.de/foerderung">passender Förderung</a>' in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "[[LINK:evil|extern]]" in html


def test_blog_content_persists_seo_snapshot_and_safe_links():
    content = build_blog_content_from_llm(
        _blog_payload(with_links=True),
        dossier_id="dossier-seo",
        seo_brief=_seo_brief(),
    )
    assert content["seo_brief"]["primary_keyword"] == "treppenlift kosten"
    assert content["internal_links"] == _seo_brief()["internal_links"]
    assert '<a href="https://www.lippelift.de/foerderung">' in content["body_html"]


def test_repeated_selector_runs_are_stable_in_both_flag_states(monkeypatch):
    from workers import topic_seed_selector

    monkeypatch.setattr(topic_seed_selector, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(topic_seed_selector, "get_researched_topic_texts", lambda: [])
    monkeypatch.setattr(topic_seed_selector, "load_seed_topics_from_yaml", lambda: [f"Frozen {index}" for index in range(20)])
    monkeypatch.setattr(topic_seed_selector, "get_seo_seed_candidates", lambda: [f"SEO {index}" for index in range(20)])
    for enabled, expected_source, expected_prefix in ((False, "yaml_bank", "Frozen"), (True, "seo_catalog", "SEO")):
        monkeypatch.setattr(topic_seed_selector, "seo_catalog_enabled", lambda enabled=enabled: enabled)
        for _ in range(100):
            seeds, source = topic_seed_selector.select_seeds(max_topics=7)
            assert len(seeds) == len(set(seeds)) == 7
            assert source == expected_source
            assert all(seed.startswith(expected_prefix) for seed in seeds)
