from src.business.scheduling import PlanItem, plan, reschedule
from src.business.algorithm import analyze
from src.business.demo import demo_rows


def item(sample, minutes, score, group=None):
    return PlanItem(sample, minutes, score, group)


def test_budget_never_exceeded():
    items = [item(f"s{i}", 10 + i, 0.5) for i in range(10)]
    result = plan(items, 35)
    assert result.scheduled_minutes <= 35
    assert not result.over_budget


def test_required_groups_are_covered_first():
    items = [
        item("cheap", 5, 0.01),
        item("big", 40, 0.99),
        item("unseen-a", 30, 0.0, "T1 / 芬兰文"),
        item("unseen-b", 30, 0.0, "T2 / 芬兰文"),
    ]
    result = plan(items, 70, ["T1 / 芬兰文", "T2 / 芬兰文"])
    assert result.covered_groups == {"T1 / 芬兰文", "T2 / 芬兰文"}
    assert "unseen-a" in result.selected and "unseen-b" in result.selected
    assert result.missing_groups == []
    assert result.scheduled_minutes <= 70


def test_missing_groups_are_reported_when_budget_short():
    items = [
        item("a", 40, 0.9, "G1"), item("b", 40, 0.9, "G2"), item("c", 40, 0.9, "G3"),
        item("filler", 20, 0.5),
    ]
    result = plan(items, 45, ["G1", "G2", "G3"])
    assert result.missing_groups == ["G2", "G3"]
    assert result.scheduled_minutes <= 45


def test_knapsack_maximises_expected_overflows():
    items = [item("a", 10, 0.9), item("b", 10, 0.8), item("c", 20, 1.0), item("d", 5, 0.1)]
    result = plan(items, 20)
    assert set(result.selected) == {"a", "b"} or result.expected_overflows >= 1.7


def test_reschedule_keeps_started_samples_even_when_over_budget():
    items = [
        item("running", 60, 0.9), item("next", 50, 0.8), item("fill", 10, 0.2),
    ]
    result = reschedule(items, 60, fixed_ids=["running"])
    assert "running" in result.selected
    assert result.over_budget is False
    assert "next" not in result.selected  # 60 + 50 > 60


def test_reschedule_never_leaves_budget_idle_when_work_fits():
    items = [item(f"s{i}", 10, 0.1 + i / 100) for i in range(9)]
    result = reschedule(items, 55, fixed_ids=["s8"])
    assert result.scheduled_minutes == 50
    assert set(result.selected) >= {"s8"}


def test_deterministic():
    items = [item(f"s{i}", 7 + i % 3, (i * 37 % 100) / 100) for i in range(20)]
    assert plan(items, 40).ordered == plan(items, 40).ordered


def test_analyze_runs_end_to_end_on_demo():
    result = analyze(demo_rows(), 0.6, 120)
    assert result["plan"]["scheduled_minutes"] <= 120
    assert result["plan"]["model"] in {"目标编码", "几何基准"}
    # The unseen Finnish combination must be planned at any workable budget.
    assert "LY-P08" in result["plan"]["scheduled"]
    assert any("实查溢出" in metric["label"] for metric in result["metrics"])


def test_analyze_honours_tiny_shift_and_names_missing_group():
    result = analyze(demo_rows(), 0.6, 10)
    assert result["plan"]["scheduled_minutes"] <= 10
    assert result["plan"]["missing_groups"]
    assert any("缺" in note or "工时不足" in note for note in result["notes"])


def test_duration_required():
    rows = demo_rows()
    rows[-1]["est_minutes"] = None
    try:
        analyze(rows, 0.6, 120)
    except ValueError as exc:
        assert "耗时" in str(exc)
    else:
        raise AssertionError("missing duration should fail")
