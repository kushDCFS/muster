"""
Tests for the claims this tool refuses to make.

These are as important as the correctness tests. A coverage tool that
manufactures a problem at a well-run agency, or that quietly monetizes a
causal link the literature does not support, discredits every legitimate
report it produces. These lock that behavior in place.
"""

import io

import pandas as pd
import pytest

from model import audit, benchmark, impact
from api.report import render_report
from api.packet import render_packet
from model import optimizer as opt


def _wellrun(n=600):
    """An agency with fast, consistent turnout -- nothing to find."""
    rows = []
    base = pd.Timestamp("2024-01-01 08:00")
    for i in range(n):
        d = base + pd.Timedelta(hours=4 * i)
        rows.append({
            "Unit Notified By Dispatch": d.strftime("%Y-%m-%d %H:%M:%S"),
            "Unit En Route": (d + pd.Timedelta(seconds=45)).strftime("%Y-%m-%d %H:%M:%S"),
            "Disposition": "Transported",
        })
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    df, pr = audit.parse_upload(buf.getvalue().encode(), "x.csv")
    return audit.analyze(df), pr


class TestDoesNotManufactureProblems:
    def test_well_run_agency_shows_near_zero_failure(self):
        f, _ = _wellrun()
        assert f["summary"]["failure_rate"] < 0.01

    def test_report_switches_to_clean_framing(self):
        f, pr = _wellrun()
        html = render_report("t", {"findings": f, "parse_report": pr,
                                   "agency_name": "Test", "is_demo": False,
                                   "source": "t.csv"})
        assert "No significant crew-assembly problem" in html

    def test_funding_packet_refuses_to_build_a_case(self):
        """The packet must not hand a well-run agency a grant request. A
        reviewer would see through it and it would poison every other packet."""
        f, pr = _wellrun()
        plan = opt.optimize(opt.build_grid(audit.classify(pd.DataFrame({
            "dispatch_time": [pd.Timestamp("2024-01-01 10:00")],
            "enroute_time": [pd.Timestamp("2024-01-01 10:01")],
        }))), 20)
        html = render_packet("t", {"findings": f, "parse_report": pr,
                                   "agency_name": "Test", "is_demo": False,
                                   "source": "t.csv"}, plan)
        assert "NOT A FUNDING CASE" in html

    def test_no_staffing_cause_asserted_from_immaterial_rates(self):
        """A 1.5x ratio between 0.2% and 0.1% is noise. Claiming a volunteer
        roster explains it would be an unsupported causal assertion."""
        f, pr = _wellrun()
        plan = opt.optimize(opt.build_grid(audit.classify(pd.DataFrame({
            "dispatch_time": [pd.Timestamp("2024-01-01 10:00")],
            "enroute_time": [pd.Timestamp("2024-01-01 10:01")],
        }))), 20)
        html = render_packet("t", {"findings": f, "parse_report": pr,
                                   "agency_name": "T", "is_demo": False,
                                   "source": "t.csv"}, plan)
        assert "outside daytime employment" not in html


class TestNoOutcomeClaims:
    """The response-time-to-survival literature (115 studies, 691,056
    patients) does not support an outcome claim at these margins."""

    FORBIDDEN = ["lives saved", "value of statistical life", "vsl",
                 "deaths prevented", "mortality reduction"]

    def test_impact_model_computes_no_outcome_figure(self):
        cov = impact.coverage_impact(400, 100, 3000)
        prev = impact.prevention_impact(1800)
        work = impact.workforce_impact(8)
        blob = " ".join(str(x) for x in (cov, prev, work)).lower()
        for term in self.FORBIDDEN:
            assert term not in blob, f"outcome claim leaked: {term}"

    def test_report_makes_no_outcome_claim(self):
        f, pr = _wellrun()
        html = render_report("t", {"findings": f, "parse_report": pr,
                                   "agency_name": "T", "is_demo": False,
                                   "source": "t.csv"}).lower()
        for term in self.FORBIDDEN:
            assert term not in html, f"outcome claim leaked: {term}"


class TestPreventionCaseloadRealism:
    def test_savings_capped_at_a_real_caseload(self):
        """Multiplying per-person savings by every flagged resident implies
        enrolling a large share of a county, which no program does."""
        r = impact.prevention_impact(1890)
        assert r["realistic_enrollment"] == impact.CASELOAD_PER_PARAMEDIC
        assert r["savings_high"] < 1_000_000

    def test_flagged_count_still_reported(self):
        r = impact.prevention_impact(1890)
        assert r["residents_flagged"] == 1890


class TestBenchmarkPrivacy:
    def test_withholds_percentile_below_minimum_cohort(self):
        f, _ = _wellrun()
        c = benchmark.compare(f, "ems")
        if not c["available"]:
            assert c["cohort_size"] < c["min_required"]
            assert "failure_rate_percentile" not in c

    def test_cohort_row_carries_no_identity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(benchmark, "COHORT_PATH", str(tmp_path / "c.json"))
        f, _ = _wellrun()
        row = benchmark.contribute(f, "ems")
        for field in ("agency_name", "name", "location", "county", "incident_id"):
            assert field not in row
        assert set(row) <= {"service_type", "band", "calls_per_year", "failure_rate",
                            "median_assembly_min", "workday_penalty", "added", "label"}


class TestDispositionReviewNotGuessing:
    """Unrecognized disposition codes must be surfaced, never guessed at. A
    single misclassified code moves the headline failure rate by ~45x on the
    reference dataset, and that number lands in a funding request."""

    def _frame(self, codes):
        rows = [{"Incident": f"I{i}",
                 "Unit Notified By Dispatch": f"2024-03-{i%28+1:02d} 10:00:00",
                 "Unit En Route": f"2024-03-{i%28+1:02d} 10:02:00",
                 "Disposition": c}
                for i, c in enumerate(codes)]
        import io as _io
        buf = _io.StringIO(); pd.DataFrame(rows).to_csv(buf, index=False)
        df, _ = audit.parse_upload(buf.getvalue().encode(), "x.csv")
        return df

    def test_unrecognized_codes_are_reported(self):
        df = self._frame(["10-22"] * 10 + ["Transported"] * 90)
        r = audit.analyze(df)["disposition_review"]
        codes = {c["code"]: c for c in r["codes"]}
        assert codes["10-22"]["counts_as_no_crew"] is False
        assert codes["10-22"]["matched_by"] is None
        assert r["unreviewed_incidents"] == 100

    def test_unrecognized_code_is_not_silently_counted_as_failure(self):
        df = self._frame(["10-22"] * 10 + ["Transported"] * 90)
        assert audit.analyze(df)["summary"]["failures_total"] == 0

    def test_agency_supplied_code_is_honoured(self):
        df = self._frame(["10-22"] * 10 + ["Transported"] * 90)
        f = audit.analyze(df, extra_no_crew=["10-22"])
        assert f["summary"]["failures_total"] == 10
        codes = {c["code"]: c for c in f["disposition_review"]["codes"]}
        assert codes["10-22"]["matched_by"] == "your input"

    def test_review_is_flagged_when_no_disposition_column(self):
        import io as _io
        buf = _io.StringIO()
        pd.DataFrame([{"Unit Notified By Dispatch": "2024-03-01 10:00:00"}]).to_csv(buf, index=False)
        df, _ = audit.parse_upload(buf.getvalue().encode(), "x.csv")
        assert audit.analyze(df)["disposition_review"]["available"] is False
