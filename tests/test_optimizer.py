"""Tests for coverage-block selection and the returns curve."""

import pytest

from model import optimizer as opt


def grid(failures_by_cell=None):
    """168-cell grid; failures_by_cell maps (weekday, hour) -> count."""
    g = {}
    for w in range(7):
        for h in range(24):
            g[(w, h)] = {"weekday": w, "hour": h, "incidents": 5,
                         "failures": 0, "crew_failures": 0}
    for (w, h), n in (failures_by_cell or {}).items():
        g[(w, h)]["crew_failures"] = n
        g[(w, h)]["failures"] = n
    return g


class TestBlockSelection:
    def test_finds_the_concentrated_window(self):
        """Failures parked on Tuesday morning must produce a Tuesday block."""
        g = grid({(1, h): 10 for h in range(9, 13)})
        r = opt.optimize(g, hours_budget=4)
        assert len(r["blocks"]) == 1
        b = r["blocks"][0]
        assert b["weekday"] == "Tue"
        assert b["start"] == 9 and b["end"] == 13
        assert r["failures_covered"] == 40

    def test_never_exceeds_the_hour_budget(self):
        g = grid({(w, h): 5 for w in range(7) for h in range(24)})
        for budget in (4, 7, 12, 20, 33):
            r = opt.optimize(g, hours_budget=budget)
            assert r["hours_used"] <= budget

    def test_blocks_are_contiguous_and_within_one_day(self):
        """Nobody staffs scattered single hours, and a shift that crosses
        midnight is a different scheduling decision."""
        g = grid({(2, h): 8 for h in range(20, 24)})
        r = opt.optimize(g, hours_budget=8)
        for b in r["blocks"]:
            assert b["end"] > b["start"]
            assert b["end"] <= 24
            assert b["length"] == b["end"] - b["start"]

    def test_blocks_never_overlap(self):
        g = grid({(1, h): 10 for h in range(6, 18)})
        r = opt.optimize(g, hours_budget=24)
        covered = set()
        for b in r["blocks"]:
            cells = {(b["weekday"], h) for h in range(b["start"], b["end"])}
            assert not (cells & covered), "blocks overlap"
            covered |= cells

    def test_returns_nothing_when_there_are_no_failures(self):
        r = opt.optimize(grid(), hours_budget=40)
        assert r["blocks"] == []
        assert r["failures_covered"] == 0
        assert r["coverage_pct"] == 0.0

    def test_budget_below_minimum_block_buys_nothing(self):
        g = grid({(1, 10): 50})
        r = opt.optimize(g, hours_budget=opt.MIN_BLOCK - 1)
        assert r["blocks"] == []


class TestCost:
    def test_cost_scales_with_hours_used(self):
        g = grid({(1, h): 10 for h in range(8, 20)})
        r = opt.optimize(g, hours_budget=8, cost_per_hour=50)
        assert r["weekly_cost"] == r["hours_used"] * 50
        assert r["annual_cost"] == r["hours_used"] * 50 * 52

    def test_cost_per_failure_is_none_when_nothing_covered(self):
        r = opt.optimize(grid(), hours_budget=20)
        assert r["cost_per_failure_prevented"] is None


class TestReturnsCurve:
    def test_coverage_is_monotonic_in_budget(self):
        """More staffed hours can never cover fewer failures."""
        g = grid({(w, h): (w + 1) * (h % 7) for w in range(7) for h in range(24)})
        pts = opt.returns_curve(g, max_hours=48)["points"]
        cov = [p["coverage_pct"] for p in pts]
        assert cov == sorted(cov)

    def test_curve_reports_marginal_gain(self):
        g = grid({(1, h): 10 for h in range(8, 16)})
        pts = opt.returns_curve(g, max_hours=24)["points"]
        assert all("marginal_gain" in p for p in pts)
        assert pts[0]["marginal_gain"] == pts[0]["coverage_pct"]

    def test_knee_lands_where_returns_flatten(self):
        """All failures in one 8-hour window: past 8 hours, extra staffing
        buys nothing, so the knee should be at or near that point."""
        g = grid({(3, h): 20 for h in range(9, 17)})
        curve = opt.returns_curve(g, max_hours=48)
        assert curve["knee_hours"] is not None
        assert curve["knee_hours"] <= 16
