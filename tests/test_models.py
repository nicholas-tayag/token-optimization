from agenvantage.models import ContextComponent, ContextScenario


def test_component_parses_boolean_like_flags_and_cleans_labels() -> None:
    component = ContextComponent.from_dict(
        {
            "id": "policy",
            "text": "Never disclose secrets.",
            "required": "true",
            "stable": "0",
            "labels": [" policy ", "", "security", 7],
        }
    )

    assert component.required is True
    assert component.stable is False
    assert component.labels == ("policy", "security", "7")


def test_component_accepts_scalar_labels_and_blank_numeric_fields() -> None:
    component = ContextComponent.from_dict(
        {
            "id": "memory",
            "text": "Keep prior context.",
            "priority": " ",
            "relevance": "",
            "labels": "memory",
        }
    )

    assert component.priority == 0
    assert component.relevance == 0.0
    assert component.labels == ("memory",)


def test_scenario_accepts_id_alias_and_strips_whitespace() -> None:
    scenario = ContextScenario.from_dict(
        {
            "id": "  synthetic-scenario  ",
            "description": "desc",
            "components": [{"id": "request", "text": "Investigate the issue."}],
        }
    )

    assert scenario.scenario_id == "synthetic-scenario"


def test_scenario_requires_non_empty_identifier() -> None:
    try:
        ContextScenario.from_dict(
            {
                "scenario_id": "   ",
                "components": [{"id": "request", "text": "Investigate the issue."}],
            }
        )
    except ValueError as error:
        assert str(error) == "Scenarios require a non-empty scenario_id or id field."
    else:
        raise AssertionError("Expected blank scenario identifiers to be rejected.")


def test_scenario_requires_components_list() -> None:
    try:
        ContextScenario.from_dict({"scenario_id": "demo", "components": None})
    except ValueError as error:
        assert str(error) == "Scenarios require a components list."
    else:
        raise AssertionError("Expected missing component lists to be rejected.")
