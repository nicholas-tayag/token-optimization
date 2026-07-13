from __future__ import annotations

from agenvantage.pruning import goal_hints_from_context, prune_lines


def test_prune_lines_keeps_definition_and_matching_terms() -> None:
    text = "\n".join(
        [
            "import click",
            "",
            "def update_min_steps(self):",
            "    self._completed_intervals += 1",
            "    return self._completed_intervals",
            "",
            "# filler comment without keywords",
            "x = 1",
            "y = 2",
        ]
    )
    hints = goal_hints_from_context(
        "Fix progress bar update_min_steps final position",
        change_surface={
            "edit_targets": [{"path": "src/click/_termui_impl.py"}],
            "test_targets": [{"path": "tests/test_termui.py"}],
        },
    )
    pruned, meta = prune_lines(text, goal_hints=hints, min_score=1.0)

    assert "update_min_steps" in pruned
    assert "_completed_intervals" in pruned
    assert meta["dropped_lines"] >= 1
    assert "x = 1" not in pruned


def test_prune_lines_keeps_all_when_everything_matches() -> None:
    text = "def run():\n    return 1\n"
    hints = goal_hints_from_context("Explain run()")
    pruned, meta = prune_lines(text, goal_hints=hints, min_score=10.0)
    assert pruned == text
    assert meta["kept_lines"] == 2
