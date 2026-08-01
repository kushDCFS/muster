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
