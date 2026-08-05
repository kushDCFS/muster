"""Tests for dispatch-export parsing and crew-assembly classification."""

import io

import pandas as pd
import pytest

from model import audit


def _csv(rows, header):
    buf = io.StringIO()
    buf.write(header + "\n")
    for r in rows:
        buf.write(r + "\n")
    return buf.getvalue().encode("utf-8")


class TestParsing:
    def test_matches_common_vendor_column_names(self):
        """Agencies export from ESO/ImageTrend/Zoll/county CAD with different
        headers. All must resolve without the user renaming anything."""
        for dispatch_col, enroute_col in [
            ("Unit Notified By Dispatch", "Unit En Route"),
            ("dispatch_dttm", "response_dttm"),
            ("Time Dispatched", "Time Enroute"),
            ("alarm_time", "turnout_time"),
        ]:
            raw = _csv(
                ["2024-03-01 10:00:00,2024-03-01 10:04:00"],
                f"{dispatch_col},{enroute_col}",
            )
            df, report = audit.parse_upload(raw, "x.csv")
            assert "dispatch_time" in df.columns, dispatch_col
            assert "enroute_time" in df.columns, enroute_col

    def test_drops_patient_columns_before_analysis(self):
        """Privacy is enforced at parse time, not by convention. A careless
        export must not be able to drag patient data into the tool."""
        raw = _csv(
            ["1,2024-03-01 10:00:00,Jane Doe,1975-04-02,chest pain,555-0100"],
            "Incident,Unit Notified By Dispatch,PatientFirstName,DOB,ChiefComplaint,Phone",
        )
        df, report = audit.parse_upload(raw, "x.csv")

        dropped = report["columns_dropped_as_phi"]
        assert set(dropped) == {"PatientFirstName", "DOB", "ChiefComplaint", "Phone"}
        for col in dropped:
            assert col not in df.columns

    def test_rejects_file_with_no_dispatch_timestamp(self):
        raw = _csv(["MEDIC-1,Transported"], "Unit,Disposition")
        with pytest.raises(ValueError, match="dispatch"):
            audit.parse_upload(raw, "x.csv")

    def test_rows_without_parseable_dispatch_time_are_excluded(self):
        raw = _csv(
            ["2024-03-01 10:00:00", "not-a-date", "2024-03-02 11:00:00"],
            "Unit Notified By Dispatch",
        )
        df, report = audit.parse_upload(raw, "x.csv")
        assert report["rows_parsed"] == 2


class TestClassification:
    def _frame(self, pairs):
        """pairs: (dispatch, enroute_or_None) -> classified frame."""
        rows = []
        for d, e in pairs:
            rows.append({"dispatch_time": pd.Timestamp(d),
                         "enroute_time": pd.Timestamp(e) if e else pd.NaT})
        return audit.classify(pd.DataFrame(rows))

    def test_fast_turnout_is_normal(self):
        c = self._frame([("2024-03-01 10:00", "2024-03-01 10:02")])
        assert c.normal.iloc[0]
        assert not c.delayed.iloc[0]
        assert not c.no_crew.iloc[0]

    def test_turnout_bands(self):
        c = self._frame([
            ("2024-03-01 10:00", "2024-03-01 10:03"),   # 3 min  -> normal
            ("2024-03-01 11:00", "2024-03-01 11:07"),   # 7 min  -> delayed
            ("2024-03-01 12:00", "2024-03-01 12:14"),   # 14 min -> severe
            ("2024-03-01 13:00", "2024-03-01 13:25"),   # 25 min -> no crew
        ])
        assert list(c.normal) == [True, False, False, False]
        assert list(c.delayed) == [False, True, False, False]
        assert list(c.severe_delay) == [False, False, True, False]
        assert list(c.no_crew) == [False, False, False, True]

    def test_negative_and_absurd_intervals_are_discarded(self):
        """Clock skew and merge artifacts must not be counted as instant
        turnout or as multi-day failures."""
        c = self._frame([
            ("2024-03-01 10:00", "2024-03-01 09:50"),   # enroute before dispatch
            ("2024-03-01 11:00", "2024-03-05 11:00"),   # 4 days later
        ])
        assert c.assembly_min.isna().all()

    def test_mutual_aid_disposition_counts_as_no_crew(self):
        df = pd.DataFrame([{
            "dispatch_time": pd.Timestamp("2024-03-01 10:00"),
            "enroute_time": pd.NaT,
            "disposition": "Mutual Aid - No Unit Available",
        }])
        assert audit.classify(df).no_crew.iloc[0]


class TestAnalysis:
    def _synthetic(self, n=400, fail_every=10):
        rows = []
        base = pd.Timestamp("2024-01-01 09:00")
        for i in range(n):
            d = base + pd.Timedelta(hours=6 * i)
            enroute = None if i % fail_every == 0 else d + pd.Timedelta(minutes=3)
            rows.append({
                "Unit Notified By Dispatch": d.strftime("%Y-%m-%d %H:%M:%S"),
                "Unit En Route": enroute.strftime("%Y-%m-%d %H:%M:%S") if enroute else "",
                "Disposition": "Mutual Aid - No Unit" if enroute is None else "Transported",
            })
        buf = io.StringIO()
        pd.DataFrame(rows).to_csv(buf, index=False)
        df, _ = audit.parse_upload(buf.getvalue().encode(), "x.csv")
        return audit.analyze(df)

    def test_produces_full_168_cell_grid(self):
        f = self._synthetic()
        assert len(f["grid"]) == 168

    def test_failure_rate_matches_injected_rate(self):
        f = self._synthetic(n=400, fail_every=10)
        assert f["summary"]["failure_rate"] == pytest.approx(0.10, abs=0.02)

    def test_nfpa_compliance_is_reported(self):
        f = self._synthetic()
        nfpa = f["nfpa_1720"]
        assert nfpa["target_seconds"] in (60, 90)
        assert 0.0 <= nfpa["compliance_rate"] <= 1.0

    def test_worst_windows_exclude_thin_cells(self):
        """A cell with one or two incidents cannot support a rate; ranking on
        it would surface noise as the agency's worst problem."""
        f = self._synthetic()
        assert all(w["incidents"] >= 3 for w in f["pattern"]["worst_windows"])


class TestServiceTypeDetection:
    def test_detects_fire_from_incident_types(self):
        df = pd.DataFrame({
            "dispatch_time": [pd.Timestamp("2024-03-01 10:00")] * 4,
            "incident_type": ["Structure Fire", "Outside Fire", "Alarms", "Smoke"],
        })
        kind, mix = audit.detect_service_type(df)
        assert kind == "fire"

    def test_detects_ems_from_incident_types(self):
        df = pd.DataFrame({
            "dispatch_time": [pd.Timestamp("2024-03-01 10:00")] * 4,
            "incident_type": ["Medical Incident", "Fall", "Cardiac", "Breathing"],
        })
        kind, mix = audit.detect_service_type(df)
        assert kind == "ems"

    def test_defaults_to_ems_without_incident_type(self):
        df = pd.DataFrame({"dispatch_time": [pd.Timestamp("2024-03-01 10:00")]})
        kind, mix = audit.detect_service_type(df)
        assert kind == "ems"
        assert mix is None


class TestIncidentCollapse:
    def test_multi_unit_rows_collapse_to_one_incident(self):
        """CAD emits one row per responding unit. Coverage is an incident-level
        property, so extra units on a call already counted must not inflate the
        denominator or score a slow second-due unit as a failure."""
        raw = _csv([
            "A1,2024-03-01 10:00:00,2024-03-01 10:02:00",
            "A1,2024-03-01 10:00:00,2024-03-01 10:19:00",
            "A2,2024-03-01 12:00:00,2024-03-01 12:03:00",
        ], "Incident,Unit Notified By Dispatch,Unit En Route")
        df, _ = audit.parse_upload(raw, "x.csv")
        out, info = audit.collapse_to_incidents(df)

        assert info["collapsed"] is True
        assert info["extra_unit_rows"] == 1
        assert len(out) == 2
        # the FIRST unit en route defines the incident, not the slowest
        a1 = out[out.incident_id == "A1"].iloc[0]
        assert (a1.enroute_time - a1.dispatch_time).total_seconds() / 60 == 2

    def test_no_crew_signal_survives_the_collapse(self):
        raw = _csv([
            "B1,2024-03-01 10:00:00,,Mutual Aid - No Unit Available",
            "B1,2024-03-01 10:00:00,2024-03-01 10:30:00,Transported",
        ], "Incident,Unit Notified By Dispatch,Unit En Route,Disposition")
        df, _ = audit.parse_upload(raw, "x.csv")
        out, _ = audit.collapse_to_incidents(df)
        assert audit.classify(out).no_crew.iloc[0]

    def test_single_unit_export_is_left_alone(self):
        raw = _csv(["C1,2024-03-01 10:00:00", "C2,2024-03-02 10:00:00"],
                   "Incident,Unit Notified By Dispatch")
        df, _ = audit.parse_upload(raw, "x.csv")
        out, info = audit.collapse_to_incidents(df)
        assert info["collapsed"] is False
        assert len(out) == 2


class TestUncertainty:
    def test_wilson_interval_brackets_the_estimate(self):
        lo, hi = audit.wilson_interval(58, 37733)
        assert lo < 58 / 37733 < hi

    def test_interval_never_leaves_zero_to_one(self):
        for k, n in [(0, 100), (100, 100), (1, 3), (0, 1)]:
            lo, hi = audit.wilson_interval(k, n)
            assert 0.0 <= lo <= hi <= 1.0

    def test_small_samples_get_wider_intervals(self):
        """A 40-call agency must not be handed the same false precision as a
        40,000-call one."""
        n_lo, n_hi = audit.wilson_interval(4, 40)
        w_lo, w_hi = audit.wilson_interval(4000, 40000)
        assert (n_hi - n_lo) > (w_hi - w_lo) * 5

    def test_analysis_reports_a_confidence_interval(self):
        raw = _csv([f"I{i},2024-03-{i%28+1:02d} 10:00:00,2024-03-{i%28+1:02d} 10:03:00"
                    for i in range(200)],
                   "Incident,Unit Notified By Dispatch,Unit En Route")
        df, _ = audit.parse_upload(raw, "x.csv")
        s = audit.analyze(df)["summary"]
        lo, hi = s["failure_rate_ci95"]
        assert lo <= s["failure_rate"] <= hi
