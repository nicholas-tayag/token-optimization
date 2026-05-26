from agenvantage.models import ContextComponent, ContextScenario
from agenvantage.policies import BudgetedContextPolicy, CacheAlignedPolicy, FullContextPolicy
from agenvantage.tokenizer import TokenCounter


def scenario() -> ContextScenario:
    return ContextScenario(
        scenario_id="test",
        description="test data",
        components=(
            ContextComponent("dynamic", "request", "Investigate the current issue.", True, False),
            ContextComponent("policy", "instruction", "Never disclose secrets.", True, True),
            ContextComponent(
                "signal",
                "retrieval",
                "Latency rose after deployment.",
                False,
                False,
                90,
                0.95,
            ),
            ContextComponent(
                "noise",
                "memory",
                "An unrelated incident happened in an older service. " * 25,
                False,
                False,
                1,
                0.05,
            ),
        ),
    )


def test_full_context_preserves_source_order() -> None:
    package = FullContextPolicy().assemble(scenario(), TokenCounter())
    assert [item.component_id for item in package.included] == [
        "dynamic",
        "policy",
        "signal",
        "noise",
    ]
    assert package.stable_prefix_tokens == 0


def test_cache_aligned_moves_stable_context_to_prefix() -> None:
    package = CacheAlignedPolicy().assemble(scenario(), TokenCounter())
    assert package.included[0].component_id == "policy"
    assert package.stable_prefix_tokens > 0


def test_budgeted_policy_includes_required_and_drops_low_value_context() -> None:
    counter = TokenCounter()
    mandatory = CacheAlignedPolicy().assemble(
        ContextScenario("required", "", scenario().components[:2]), counter
    )
    package = BudgetedContextPolicy(mandatory.input_tokens + 30).assemble(
        scenario(), counter
    )
    included_ids = {item.component_id for item in package.included}
    excluded_ids = {item.component_id for item in package.excluded}
    assert {"dynamic", "policy"}.issubset(included_ids)
    assert "noise" in excluded_ids
    assert package.input_tokens <= package.budget

