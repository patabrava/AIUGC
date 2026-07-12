import pytest

from app.core.errors import ValidationError


def test_edit_metrics_derive_true_shot_distribution_and_density():
    from app.features.shot_production.reference_comparison import derive_edit_metrics

    metrics = derive_edit_metrics(14.5, [7.25])

    assert metrics["cut_count"] == 1
    assert metrics["cut_density_per_second"] == pytest.approx(1 / 14.5)
    assert metrics["shot_durations_seconds"] == pytest.approx([7.25, 7.25])
    assert metrics["mean_shot_duration_seconds"] == pytest.approx(7.25)
    assert metrics["shot_duration_cv"] == pytest.approx(0.0)


def test_comparison_marks_two_shot_candidate_closer_than_four_take_control():
    from app.features.shot_production.reference_comparison import compare_edit_profiles

    report = compare_edit_profiles(
        reference=derive(53.823855, [9.566667, 19.233333, 27.433333, 37.0, 46.766667]),
        control=derive(14.5, [3.666667, 7.375, 11.458333]),
        candidate=derive(14.5, [7.25]),
    )

    assert report["candidate"]["cut_count"] == 1
    assert report["closer_to_reference_than_control"] is True
    assert report["candidate_reference_distance"] < report["control_reference_distance"]
    assert report["candidate_two_shot_gate"] == {"passed": True, "failure_reasons": []}


def derive(duration, cuts):
    from app.features.shot_production.reference_comparison import derive_edit_metrics

    return derive_edit_metrics(duration, cuts)


@pytest.mark.parametrize(
    ("duration", "cuts"),
    [
        (0.0, []),
        (10.0, [5.0, 4.0]),
        (10.0, [0.0]),
        (10.0, [10.0]),
    ],
)
def test_edit_metrics_reject_invalid_duration_or_cut_timeline(duration, cuts):
    from app.features.shot_production.reference_comparison import derive_edit_metrics

    with pytest.raises(ValidationError):
        derive_edit_metrics(duration, cuts)
