"""Regression tests for topic bank selection in S1 batch seeding."""

from app.features.topics import handlers as topic_handlers


def test_unique_topic_suggestions_scans_past_early_semantic_duplicates():
    suggestions = [
        {
            "title": f"Wheelchair curb technique {index}",
            "rotation": "Use the same safe curb approach every time.",
            "cta": "Practice the safe curb approach.",
            "script": "Use the same safe curb approach every time.",
            "family_fingerprint": f"family-{index}",
        }
        for index in range(3)
    ]
    suggestions.extend(
        [
            {
                "title": "Doorway clearance checklist",
                "rotation": "Measure the narrowest doorway before buying equipment.",
                "cta": "Save the doorway width before your next appointment.",
                "script": "Measure the narrowest doorway before buying equipment.",
                "family_fingerprint": "family-doorway",
            },
            {
                "title": "Ramp angle quick check",
                "rotation": "Check the ramp angle with a phone before committing.",
                "cta": "Test the ramp angle before you roll.",
                "script": "Check the ramp angle with a phone before committing.",
                "family_fingerprint": "family-ramp",
            },
        ]
    )

    result = topic_handlers._unique_topic_suggestions(suggestions, 3, existing_topics=[])

    assert [row["family_fingerprint"] for row in result] == [
        "family-0",
        "family-doorway",
        "family-ramp",
    ]
