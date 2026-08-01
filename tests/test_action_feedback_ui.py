from pathlib import Path


def test_base_loads_shared_immediate_action_feedback():
    base = Path("templates/base.html").read_text(encoding="utf-8")
    source = Path("static/js/action_feedback.js").read_text(encoding="utf-8")

    assert "/static/js/action_feedback.js" in base
    assert "window.beginActionFeedback" in source
    assert "window.endActionFeedback" in source
    assert "htmx:beforeRequest" in source
    assert "htmx:afterRequest" in source
    assert "const button = initiatingButton(event.detail?.elt);\n        if (!button?.isConnected) return;" in source
    assert "event.submitter" in source
    assert "data-action-pending" in source
    assert "aria-busy" in source


def test_semantic_actions_use_shared_feedback_with_specific_labels():
    source = Path("static/js/batches/semantic_video.js").read_text(encoding="utf-8")

    assert "window.beginActionFeedback(button, pendingLabel)" in source
    assert "window.endActionFeedback(button, feedbackState)" in source
    assert "Approving scene plate…" in source
    assert "Building plan…" in source
    assert "Starting generation…" in source
    assert "Starting retry…" in source
    assert "Continuing QA…" in source
