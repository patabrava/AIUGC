from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.features.blog.schemas import BlogInternalLink, render_body_html
from app.features.topics import handlers as topic_handlers
from app.features.topics import prompts
from app.features.topics import seo_catalog


def test_catalog_reads_and_briefs_are_deterministic_under_concurrency():
    topic = "Was kostet ein Treppenlift? Preise und Einflussfaktoren"
    expected = json.dumps(seo_catalog.build_seo_brief(topic), ensure_ascii=False, sort_keys=True)

    def read_snapshot(_: int) -> tuple[int, int, str]:
        status = seo_catalog.get_catalog_status()
        brief = json.dumps(seo_catalog.build_seo_brief(topic), ensure_ascii=False, sort_keys=True)
        return status["keyword_count"], status["internal_link_count"], brief

    with ThreadPoolExecutor(max_workers=32) as executor:
        snapshots = list(executor.map(read_snapshot, range(5_000)))

    assert len(snapshots) == 5_000
    assert set(snapshots) == {(135, 12, expected)}


def test_every_curated_workbook_title_maps_back_to_its_keyword():
    checked = 0
    for entry in seo_catalog.load_keyword_catalog()["entries"]:
        for curated_row in entry["curated_rows"]:
            matched = seo_catalog.find_keyword_entry(curated_row["title"])
            assert matched is not None
            assert matched["keyword"] == entry["keyword"]
            checked += 1

    assert checked == 54


def test_disabled_topic_bank_preserves_identity_and_order_for_ten_thousand_reads(monkeypatch):
    monkeypatch.setattr(prompts, "seo_catalog_enabled", lambda: False)
    frozen = prompts._load_topic_bank_payload()
    expected_topics = frozen["topics"]

    for _ in range(10_000):
        assert prompts.get_topic_bank() is frozen
        assert prompts.get_topic_seed_catalog() == expected_topics


def test_blog_renderer_survives_one_thousand_mixed_safe_and_hostile_link_tokens():
    paragraphs = []
    for index in range(500):
        paragraphs.append(f"Sicher {index}: [[LINK:funding|Förderung & Beratung]]")
        paragraphs.append(
            f'Feindlich {index}: <script>alert({index})</script> '
            f'[[LINK:unknown|<img src=x onerror=alert({index})>]]'
        )

    rendered = render_body_html(
        intro_heading="Belastungstest",
        introduction_paragraphs=paragraphs,
        sections=[],
        conclusion_heading=None,
        conclusion_paragraphs=[],
        internal_links=[
            {"id": "funding", "title": "Förderung", "url": "https://www.lippelift.de/foerderung"}
        ],
    )

    assert rendered.count('<a href="https://www.lippelift.de/foerderung">') == 500
    assert "<script>" not in rendered
    assert "<img" not in rendered
    assert rendered.count("&lt;script&gt;") == 500
    assert rendered.count("[[LINK:unknown|") == 500


@pytest.mark.parametrize(
    "hostile_url",
    [
        "http://www.lippelift.de/foerderung",
        "https://lippelift.de/foerderung",
        "https://www.lippelift.de.evil.example/foerderung",
        "https://evil.example/?next=https://www.lippelift.de/foerderung",
        "javascript:https://www.lippelift.de/foerderung",
    ],
)
def test_internal_link_boundary_rejects_host_and_scheme_lookalikes(hostile_url):
    with pytest.raises(ValidationError):
        BlogInternalLink(id="hostile", title="Nicht erlaubt", url=hostile_url)


def test_every_search_led_topic_keeps_seo_data_out_of_actor_script_prompts():
    checked = 0
    for title in seo_catalog.get_seo_seed_candidates():
        brief = seo_catalog.build_seo_brief(title)
        dossier = {
            "topic": title,
            "seed_topic": title,
            "source_summary": "Belastbare neutrale Recherchegrundlage.",
            "framework_candidates": ["PAL"],
            "facts": ["Die konkrete Situation bestimmt die passende Einordnung."],
            "risk_notes": ["Keine pauschalen Versprechen machen."],
            "seo_brief": brief,
        }
        lane = {
            "title": title,
            "lane_family": "ratgeber",
            "angle": "Sachliche Einordnung",
            "facts": dossier["facts"],
        }
        actor_prompt = prompts.build_prompt1(
            post_type="value",
            desired_topics=1,
            dossier=dossier,
            lane_candidate=lane,
        )

        assert "SEO-DATEN:" not in actor_prompt
        assert "Nebenkeywords:" not in actor_prompt
        assert "search_volume" not in actor_prompt
        assert "metrics_as_of" not in actor_prompt
        assert "https://www.lippelift.de/" not in actor_prompt
        checked += 1

    assert checked == len(seo_catalog.get_seo_seed_candidates()) == 42


def test_topics_hydration_renders_five_hundred_parallel_seo_responses(monkeypatch):
    brief = seo_catalog.build_seo_brief("Was kostet ein Treppenlift? Preise und Einflussfaktoren")
    payload = {
        "filters": {"topic_mode": "basic"},
        "topics": [],
        "basic_topics": [
            {
                "title": "Was kostet ein Treppenlift? Preise und Einflussfaktoren",
                "post_type": "bank",
                "seo_brief": brief,
            }
        ],
        "generated_topics": [],
        "basic_topic_count": 151,
        "generated_topic_count": 0,
        "total_topics": 151,
        "active_runs": [],
        "seo_catalog_status": {
            "enabled": True,
            "keyword_count": 135,
            "internal_link_count": 12,
        },
    }
    monkeypatch.setattr(topic_handlers, "build_topic_hub_payload", lambda request: payload)

    app = FastAPI()
    app.include_router(topic_handlers.router)
    client = TestClient(app)

    def hydrate(_: int) -> tuple[int, bool, bool, bool]:
        response = client.get("/topics/hydrate", headers={"accept": "text/html"})
        return (
            response.status_code,
            "SEO catalog active" in response.text,
            "treppenlift kosten" in response.text,
            "9900 searches" in response.text,
        )

    with ThreadPoolExecutor(max_workers=32) as executor:
        results = list(executor.map(hydrate, range(500)))

    assert set(results) == {(200, True, True, True)}


def test_all_keyword_entries_build_bounded_verified_briefs():
    checked = 0
    for entry in seo_catalog.load_keyword_catalog()["entries"]:
        brief = seo_catalog.build_seo_brief(entry["keyword"])
        link_ids = [link["id"] for link in brief["internal_links"]]

        assert brief["primary_keyword"] == entry["keyword"]
        assert brief["source_kind"] == "catalog"
        assert len(brief["secondary_keywords"]) <= 6
        assert len(brief["internal_links"]) <= 3
        assert len(link_ids) == len(set(link_ids))
        assert all(link["url"].startswith("https://www.lippelift.de/") for link in brief["internal_links"])
        assert "Sarah" not in json.dumps(brief, ensure_ascii=False)
        checked += 1

    assert checked == 135
