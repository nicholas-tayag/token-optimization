from agenvantage.context_planner import detect_task_shapes, normalize_concepts, plan_context


def names(plan):
    return {slot.name for slot in plan.evidence_slots}


def test_unseen_ui_feature_has_feature_evidence_and_path():
    plan = plan_context("Implement an unread notification badge in src/ui/Inbox.tsx with tests")
    assert plan.shapes == ("feature",)
    assert "src/ui/Inbox.tsx" in plan.paths
    assert {"implementation", "state/data", "integration/render", "analogous behavior", "validation/tests", "repo constraints"} <= names(plan)
    assert "style" in names(plan)
    assert "config" not in names(plan)
    assert "schema" not in names(plan)
    assert "migration" not in names(plan)


def test_debug_stack_trace_extracts_identifier_and_runtime_slot():
    plan = plan_context("Debug ValueError in agenvantage.session.SessionManager from traceback")
    assert "debug" in plan.shapes
    assert "agenvantage.session.SessionManager" in plan.identifiers
    assert "runtime failure evidence" in names(plan)


def test_review_diff_is_a_review_shape():
    plan = plan_context("Review the diff for auth middleware changes")
    assert plan.shapes == ("review",)
    assert "diff and change context" in names(plan)


def test_compound_task_has_all_shapes():
    plan = plan_context("Explain and fix the bug, then implement a regression test")
    assert plan.shapes[0] == "compound"
    assert {"explain", "debug", "feature"} <= set(plan.shapes)


def test_concepts_deduplicate_stemming_and_variants():
    concepts = normalize_concepts(["notifications", "notification", "Notification", "rendering", "rendered"])
    assert [concept.canonical for concept in concepts] == ["notification", "render"]
    assert concepts[0].labels == ("notification",)


def test_workout_visualization_sentence_has_atomic_style_evidence():
    task = "Add a weekly workout streak visualization to the dashboard that shows current streak, longest streak, and missed workout days."
    plan = plan_context(task)
    assert {"weekly", "workout", "streak", "visualization", "dashboard", "longest", "missed", "day"} <= {concept.canonical for concept in plan.concepts}
    assert names(plan) >= {"style"}
    assert plan.evidence_slots[-1].name == "style"
    assert {slot.name for slot in plan.evidence_slots} & {"config", "schema", "migration"} == set()
    assert "Add" not in plan.identifiers


def test_explicit_identifier_is_retained_even_without_inferred_shape():
    plan = plan_context("Add a small change", identifiers=["Add"])
    assert plan.identifiers == ("Add",)


def test_traceback_keeps_error_and_qualified_identifier():
    plan = plan_context("Traceback raised ValueError in agenvantage.session.SessionManager")
    assert "ValueError" in plan.identifiers
    assert "agenvantage.session.SessionManager" in plan.identifiers


def test_grounding_classification_reports_missing_slots():
    partial = plan_context("Implement a search UI feature", observed_concepts=["code", "tests"])
    assert partial.grounding.status == "partial"
    assert partial.grounding.confidence > 0
    assert "integration/render" in partial.grounding.missing_slots

    grounded = plan_context(
        "Implement a search UI feature",
        observed_concepts=["implementation", "state", "render", "similar pattern", "tests", "repo constraints"],
    )
    assert grounded.grounding.status == "grounded"
    assert grounded.grounding.missing_slots == ()
    assert grounded.to_dict()["grounding"]["confidence"] == 1.0
