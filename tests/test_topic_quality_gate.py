from app.core.errors import ValidationError
from app.features.topics.topic_validation import (
    normalize_dash_separators,
    normalize_temporal_reference,
    validate_pre_persistence_topic_payload,
)


def test_normalize_dash_separators_removes_long_dash_but_keeps_hyphenated_words():
    text = "Deutschland 2026 \u2014 und rollstuhl-gerecht bleibt ein echtes Problem."
    result = normalize_dash_separators(text)
    assert "\u2014" not in result
    assert "rollstuhl-gerecht" in result
    assert "Deutschland 2026 und" in result


def test_normalize_temporal_reference_rewrites_stale_ab_phrase():
    result = normalize_temporal_reference(
        "Ab 2025 gibt es mehr Leistungen fuer barrierefreie Dienste.",
        current_year=2026,
    )
    assert "Ab 2025" not in result
    assert "Seit 2025" in result


def test_validate_pre_persistence_topic_payload_extends_short_8s_script():
    payload = validate_pre_persistence_topic_payload(
        {
            "topic": "Pflegegrad pruefen",
            "title": "Pflegegrad pruefen",
            "script": "Pruef deinen Pflegegrad jetzt sofort.",
            "caption": "Pflegegrad rechtzeitig pruefen spart Rueckfragen im Alltag.",
            "source_summary": "Pflegegrad rechtzeitig pruefen spart Rueckfragen im Alltag.",
            "disclaimer": "Keine Rechts- oder medizinische Beratung.",
        },
        target_length_tier=8,
        current_year=2026,
    )
    assert 14 <= len(payload["script"].split()) <= 18
    assert payload["script"].endswith(".")


def test_validate_pre_persistence_topic_payload_matches_published_bounds_for_all_tiers():
    eight = validate_pre_persistence_topic_payload(
        {
            "topic": "Bahnhof ohne Huerden",
            "title": "Bahnhof ohne Huerden",
            "script": "Du stolperst nicht am Gleis, sondern am kaputten Aufzug im Alltag.",
            "caption": "Barrieren im Bahnhof kosten Kraft.",
            "source_summary": "Barrieren im Bahnhof kosten Kraft und Zeit.",
            "disclaimer": "Keine Rechts- oder medizinische Beratung.",
        },
        target_length_tier=8,
        current_year=2026,
    )
    sixteen = validate_pre_persistence_topic_payload(
        {
            "topic": "Bahnhof ohne Huerden",
            "title": "Bahnhof ohne Huerden",
            "script": "Du willst einfach einsteigen, aber der Aufzug fällt aus und der Umweg kostet dir heute Kraft, Zeit und Nerven im Alltag, obwohl du nur ankommen willst.",
            "caption": "Barrieren im Bahnhof kosten Kraft.",
            "source_summary": "Barrieren im Bahnhof kosten Kraft und Zeit.",
            "disclaimer": "Keine Rechts- oder medizinische Beratung.",
        },
        target_length_tier=16,
        current_year=2026,
    )
    thirty_two = validate_pre_persistence_topic_payload(
        {
            "topic": "Bahnhof ohne Huerden",
            "title": "Bahnhof ohne Huerden",
            "script": "Du willst einfach einsteigen, aber der Aufzug fällt aus und der Umweg kostet Kraft, Zeit und Nerven, deshalb zählt barrierefreie Planung 2026 im Alltag für dich und alle anderen, die nicht noch mehr Wege verlieren wollen, wenn Bahnsteige, Eingänge und Wege nicht zusammenpassen und jede kleine Hürde den ganzen Tag kippt und Kraft kostet.",
            "caption": "Barrieren im Bahnhof kosten Kraft.",
            "source_summary": "Barrieren im Bahnhof kosten Kraft und Zeit.",
            "disclaimer": "Keine Rechts- oder medizinische Beratung.",
        },
        target_length_tier=32,
        current_year=2026,
    )
    assert 14 <= len(eight["script"].split()) <= 18
    assert 26 <= len(sixteen["script"].split()) <= 36
    assert 54 <= len(thirty_two["script"].split()) <= 74


def test_validate_pre_persistence_topic_payload_uses_lifestyle_bounds_for_lifestyle_posts():
    payload = validate_pre_persistence_topic_payload(
        {
            "topic": "Rollstuhl-Alltag",
            "title": "Rollstuhl-Alltag",
            "script": "Schon wieder ein unnötiger Umweg, aber heute planst du ihn bewusst anders und sparst dir Kraft im Alltag.",
            "caption": "Alltag mit Rollstuhl braucht kluge Planung.",
            "source_summary": "Alltag mit Rollstuhl braucht kluge Planung.",
            "disclaimer": "Keine Rechts- oder medizinische Beratung.",
        },
        target_length_tier=32,
        post_type="lifestyle",
        current_year=2026,
    )
    assert 8 <= len(payload["script"].split()) <= 66


def test_validate_pre_persistence_topic_payload_repairs_short_16s_lifestyle_script():
    payload = validate_pre_persistence_topic_payload(
        {
            "topic": "Rollstuhl-Alltag",
            "title": "Rollstuhl-Alltag",
            "script": "Der spontane Umweg kostet dich heute wieder Kraft und Zeit.",
            "caption": "Kleine Anpassungen entlasten den Alltag.",
            "source_summary": "Kleine Anpassungen entlasten den Alltag mit dem Rollstuhl deutlich.",
            "disclaimer": "Keine Rechts- oder medizinische Beratung.",
        },
        target_length_tier=16,
        post_type="lifestyle",
        current_year=2026,
    )
    assert 20 <= len(payload["script"].split()) <= 34


def test_validate_pre_persistence_topic_payload_strips_dash_from_all_text_fields():
    payload = validate_pre_persistence_topic_payload(
        {
            "topic": "MSZ \u2014 Rechte",
            "title": "MSZ \u2014 Rechte",
            "script": "Deutschland 2026 \u2014 ab 2025 gilt dein Anspruch.",
            "caption": "MSZ \u2014 Hilfe am Bahnhof.",
            "source_summary": "MSZ \u2014 Hilfe am Bahnhof spart Zeit.",
            "disclaimer": "Keine Rechts- oder medizinische Beratung.",
        },
        target_length_tier=8,
        current_year=2026,
    )
    for key in ("topic", "title", "script", "caption", "source_summary"):
        assert "\u2014" not in payload[key]
    assert "Ab 2025" not in payload["script"]
    assert "Seit 2025" in payload["script"]


def test_validate_pre_persistence_topic_payload_rejects_unrepairable_short_fragment():
    try:
        validate_pre_persistence_topic_payload(
            {
                "topic": "Bahnhof",
                "title": "Bahnhof",
                "script": "Nur Chaos.",
                "caption": "",
                "source_summary": "",
                "disclaimer": "Keine Rechts- oder medizinische Beratung.",
            },
            target_length_tier=8,
            current_year=2026,
        )
    except ValidationError as exc:
        assert exc.details.get("code") == "TOPIC_QUALITY_GATE_FAILED"
    else:
        raise AssertionError("Expected ValidationError")
