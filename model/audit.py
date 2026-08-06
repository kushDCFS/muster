"""
Retrospective crew-assembly audit.

This is the product's WEDGE: before predicting anything, measure what already
happened in an agency's own dispatch history. No forecasting, no validation
burden, no claims about response time and survival -- just a count of the
failures that occurred, when they clustered, and what they cost.

The core measurable is the DISPATCH -> EN ROUTE interval ("turnout" or "chute"
time). A staffed, in-station crew goes en route in 1-2 minutes. A volunteer
roster that has to be reached, drive to the station, and form a crew takes
5-15. A call with no en-route time at all, or one handed to mutual aid, is a
crew that never formed. That interval is present in essentially every CAD /
ePCR export and is the cleanest available proxy for the failure mode the
rural EMS literature documents as binding.

Deliberately NOT computed here: anything about patient outcome. This module
counts operational failures only. See data/DATA_SOURCES.md.

PRIVACY: this module never reads, stores, or reports any patient-level field.
Only incident timestamps, unit identifiers, and disposition codes are parsed;
everything reported is an aggregate count or a time-of-day pattern. Column
names that look like patient data are dropped at parse time.
"""

import io
import re
from datetime import datetime

import numpy as np
import pandas as pd

# --- thresholds (minutes from dispatch to en route) -----------------------
# NFPA 1720 allows volunteer departments substantially longer turnout than
# the 60-90s expected of a career, in-station crew. These bands are chosen to
# be defensible for a volunteer service, not to inflate the failure count.
ASSEMBLY_NORMAL_MAX = 5.0     # in-station or fast-forming crew
ASSEMBLY_DELAYED_MAX = 10.0   # roster reached, but slowly
# > ASSEMBLY_DELAYED_MAX  = severe delay
NO_CREW_MINUTES = 20.0        # beyond this, treat as a crew that never formed
                              # even if an en-route time was eventually logged

# Column aliases seen across ESO / ImageTrend / Zoll / county CAD exports.
COLUMN_ALIASES = {
    "incident_id": ["incident_id", "incident", "incident_number", "incidentno", "run_number",
                    "runno", "cad_number", "event_number", "call_number", "pcr_number", "id"],
    "dispatch_time": ["dispatch_time", "dispatched", "unit_notified_by_dispatch", "etimes03",
                      "time_dispatched", "dispatch_datetime", "dispatch", "notified", "toned",
                      "dispatch_dttm", "dispatchdttm", "dispatch_ts",
                      "alarm_time", "alarm_datetime", "time_alarm", "tone_time", "toneout",
                      "incident_datetime", "call_datetime"],
    "enroute_time": ["enroute_time", "en_route", "enroute", "unit_en_route", "etimes05",
                     "time_enroute", "responding", "time_responding", "en_route_datetime",
                     "response_dttm", "enroute_dttm", "responsedttm",
                     "turnout_time", "apparatus_enroute", "first_unit_enroute",
                     "first_activation_datetime"],
    "arrive_time": ["arrive_time", "arrived", "on_scene", "unit_arrived_on_scene", "etimes06",
                    "time_arrived", "arrival", "at_scene", "on_scene_dttm", "onscene_dttm"],
    "clear_time": ["clear_time", "cleared", "in_service", "unit_back_in_service", "etimes13",
                   "time_cleared", "available", "available_dttm", "clear_dttm"],
    "unit": ["unit", "unit_id", "apparatus", "vehicle", "responding_unit", "unit_call_sign"],
    "agency": ["agency", "agency_name", "department", "service", "provider"],
    "disposition": ["disposition", "call_disposition", "outcome", "edisposition",
                    "incident_disposition", "unit_disposition", "cancel_reason",
                    "call_final_disposition", "final_disposition"],
    "mutual_aid": ["mutual_aid", "mutualaid", "aid_given", "aid_received", "is_mutual_aid",
                   "mutual_aid_flag", "auto_aid", "aid_type", "mutual_aid_type"],
    # fire-service specific: NFIRS incident type tells us whether a run was a
    # fire call or an EMS call, which changes the applicable NFPA benchmark
    "incident_type": ["incident_type", "nfirs_incident_type", "call_type", "type",
                      "incident_type_code", "situation_found", "nature"],
}

# NFPA 1720 governs deployment for predominantly VOLUNTEER departments and
# exists precisely because volunteers cannot guarantee availability the way
# on-duty career staff can. Where a department does staff a station, it sets
# a 90-second turnout target for fire and 60 seconds for EMS, 90% of the time.
# We report against those explicitly, because a department that wants to
# document capability -- for a grant, or for an ISO Public Protection
# Classification review, half of which is department quality including
# staffing -- has no other way to produce the number.
NFPA_1720_TURNOUT_FIRE_SEC = 90
NFPA_1720_TURNOUT_EMS_SEC = 60
NFPA_COMPLIANCE_TARGET = 0.90

FIRE_TYPE_HINT = re.compile(
    r"(?:fire|structure|brush|wildland|vehicle\s*fire|alarm|smoke|hazmat|"
    r"extricat|rescue|explosion|arcing|odor)", re.I)
EMS_TYPE_HINT = re.compile(
    r"(?:medical|ems|injur|sick|cardiac|breathing|fall|overdose|stroke|"
    r"seizure|unconscious|bleeding|transport)", re.I)

# Anything matching these is dropped before analysis -- defense in depth so a
# careless export can't drag patient data into the tool.
PHI_PATTERNS = re.compile(
    r"(?:patient|pt_|first_?name|last_?name|middle|dob|date_of_birth|birth|ssn|social|"
    r"address|street|city|zip|postal|phone|email|mrn|medical_record|insurance|policy|"
    r"chief_?complaint|impression|narrative|diagnosis|medication|allerg|vital|"
    r"gender|sex|race|ethnic|age)", re.I)

# Disposition text that indicates no unit ever responded / crew never formed.
NO_CREW_DISPOSITIONS = re.compile(
    r"(?:no\s*unit|unable\s*to\s*respond|no\s*crew|no\s*personnel|not\s*staffed|unstaffed|"
    r"cancel.*no\s*unit|no\s*ambulance|unavailable|mutual\s*aid|auto\s*aid|"
    r"transferred\s*to|handed\s*off|out\s*of\s*service)", re.I)


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def parse_upload(raw_bytes, filename=""):
    """Parse a CSV/TSV dispatch export into a normalized frame.

    Returns (df, report) where `report` records exactly what was recognized,
    what was dropped, and what is missing -- shown to the user verbatim so
    nobody has to guess how their file was interpreted."""
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("Could not decode file as text. Please upload a CSV.")

    sep = "\t" if (filename.lower().endswith((".tsv", ".tab")) or text.count("\t") > text.count(",")) else ","
    raw = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, on_bad_lines="skip")
    if raw.empty:
        raise ValueError("File parsed but contained no rows.")

    original_cols = list(raw.columns)
    dropped_phi = [c for c in original_cols if PHI_PATTERNS.search(str(c))]
    raw = raw.drop(columns=dropped_phi, errors="ignore")

    lookup = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        alias_norms = {_norm(a) for a in aliases}
        for col in raw.columns:
            if _norm(col) in alias_norms:
                lookup[canonical] = col
                break

    if "dispatch_time" not in lookup:
        raise ValueError(
            "No dispatch/tone-out timestamp column found. Expected one of: "
            + ", ".join(COLUMN_ALIASES["dispatch_time"][:6])
            + f". Columns seen: {', '.join(map(str, raw.columns[:15]))}"
        )

    df = pd.DataFrame(index=raw.index)
    for canonical, col in lookup.items():
        df[canonical] = raw[col]

    for tcol in ("dispatch_time", "enroute_time", "arrive_time", "clear_time"):
        if tcol in df.columns:
            df[tcol] = pd.to_datetime(df[tcol], errors="coerce", format="mixed")

    df = df[df.dispatch_time.notna()].copy()
    if df.empty:
        raise ValueError("No rows had a parseable dispatch timestamp.")

    report = {
        "rows_parsed": int(len(df)),
        "rows_in_file": int(len(raw)),
        "columns_recognized": {k: str(v) for k, v in lookup.items()},
        "columns_ignored": [str(c) for c in raw.columns if c not in lookup.values()],
        "columns_dropped_as_phi": [str(c) for c in dropped_phi],
        "date_range": [df.dispatch_time.min().strftime("%Y-%m-%d"),
                       df.dispatch_time.max().strftime("%Y-%m-%d")],
        "has_enroute": bool("enroute_time" in df.columns and df.enroute_time.notna().any()),
        "has_disposition": bool("disposition" in df.columns),
        "has_mutual_aid_flag": bool("mutual_aid" in df.columns),
    }
    return df, report


def review_dispositions(df, extra_no_crew=()):
    """Every distinct disposition code, with whether we recognized it.

    Agencies use local codes. San Francisco's eight are all plain English and
    none of them match the built-in pattern; a department using "10-22" or
    "CANC-NU" for an unfilled call would match nothing either. Guessing at an
    unrecognized code is the wrong move here -- a wrong guess silently changes
    the failure count that a funding request is built on, and nobody would
    catch it.

    So unmatched codes are surfaced for the agency to classify instead. They
    know what their own codes mean; we do not. `extra_no_crew` carries their
    answer back in.
    """
    if "disposition" not in df.columns:
        return {"available": False, "codes": [],
                "note": "No disposition column in this export; classification "
                        "rests on dispatch-to-en-route timing alone."}

    extra = {str(e).strip().lower() for e in extra_no_crew if str(e).strip()}
    counts = df["disposition"].dropna().astype(str).value_counts()
    codes, unmatched = [], 0
    for code, n in counts.items():
        by_pattern = bool(NO_CREW_DISPOSITIONS.search(code))
        by_user = code.strip().lower() in extra
        if not (by_pattern or by_user):
            unmatched += int(n)
        codes.append({
            "code": code, "count": int(n),
            "counts_as_no_crew": by_pattern or by_user,
            "matched_by": "pattern" if by_pattern else ("your input" if by_user else None),
        })

    return {
        "available": True,
        "distinct_codes": len(codes),
        "codes": codes,
        "unreviewed_incidents": unmatched,
        "note": (
            f"{len(codes)} distinct disposition codes. Codes not recognized as a no-crew "
            "outcome are currently treated as covered calls. If any of them mean the call "
            "went unanswered or was handed to another agency, mark them -- otherwise this "
            "agency's failure count is understated."
        ),
    }


def collapse_to_incidents(df):
    """One row per incident, keyed on the FIRST unit to go en route.

    CAD and ePCR exports emit one row per unit dispatched, so a call with
    three responding units appears three times. Two things go wrong if that is
    analyzed as-is: the incident count is inflated (5.6% of rows in the SF
    reference set are additional units on a call already counted), and a
    second-due unit turning out slowly is scored as a coverage failure even
    though the call was covered by the first unit.

    Coverage is an incident-level property. A call is answered when the first
    crew rolls, so that is the row kept. No-crew signals (mutual aid, an
    unavailable disposition) survive the collapse if any unit row carries one.
    """
    if "incident_id" not in df.columns:
        return df, {"collapsed": False, "reason": "no incident id column"}
    if not df.incident_id.duplicated().any():
        return df, {"collapsed": False, "reason": "already one row per incident"}

    before = len(df)
    ordered = df.sort_values("enroute_time", na_position="last")         if "enroute_time" in df.columns else df

    agg = {"dispatch_time": "min"}
    for col in ("enroute_time", "arrive_time"):
        if col in ordered.columns:
            agg[col] = "first"
    for col in ("clear_time",):
        if col in ordered.columns:
            agg[col] = "max"
    for col in ("unit", "agency", "incident_type"):
        if col in ordered.columns:
            agg[col] = "first"

    out = ordered.groupby("incident_id", as_index=False).agg(agg)

    # a no-crew signal on ANY unit row applies to the incident
    for col, joiner in (("disposition", lambda s: " | ".join(sorted(set(s.dropna().astype(str))))),
                        ("mutual_aid", lambda s: "Y" if _truthy(s).any() else "N")):
        if col in ordered.columns:
            merged = ordered.groupby("incident_id")[col].apply(joiner).reset_index()
            out = out.merge(merged, on="incident_id", how="left")

    return out, {"collapsed": True, "rows_before": int(before),
                 "incidents_after": int(len(out)),
                 "extra_unit_rows": int(before - len(out))}


def wilson_interval(successes, n, z=1.96):
    """Wilson score interval for a proportion.

    Used instead of the normal approximation because failure counts here are
    small relative to n -- a rural agency might log 40 failures in 800 calls,
    where the normal interval misbehaves and can run below zero."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    d = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / d
    half = (z / d) * ((p * (1 - p) / n + z**2 / (4 * n**2)) ** 0.5)
    return (max(0.0, centre - half), min(1.0, centre + half))


def _truthy(series):
    return series.astype(str).str.strip().str.lower().isin(
        {"1", "y", "yes", "true", "t", "given", "received", "mutual aid", "mutualaid"})


def classify(df, extra_no_crew=()):
    """Label every incident by how the crew-assembly went."""
    out = df.copy()

    if "enroute_time" in out.columns:
        delta = (out.enroute_time - out.dispatch_time).dt.total_seconds() / 60.0
        # negative or absurd values are clock/merge artifacts, not real
        out["assembly_min"] = delta.where((delta >= 0) & (delta <= 24 * 60))
    else:
        out["assembly_min"] = np.nan

    flag_mutual = pd.Series(False, index=out.index)
    if "mutual_aid" in out.columns:
        flag_mutual |= _truthy(out.mutual_aid)
    if "disposition" in out.columns:
        disp = out.disposition.astype(str)
        flag_mutual |= disp.str.contains(NO_CREW_DISPOSITIONS, na=False)
        extra = {str(e).strip().lower() for e in extra_no_crew if str(e).strip()}
        if extra:
            flag_mutual |= disp.str.strip().str.lower().isin(extra)

    never_enroute = out.assembly_min.isna() & (
        out.enroute_time.isna() if "enroute_time" in out.columns else True)
    too_long = out.assembly_min > NO_CREW_MINUTES

    out["no_crew"] = flag_mutual | too_long | (never_enroute & flag_mutual)
    out["severe_delay"] = (~out.no_crew) & (out.assembly_min > ASSEMBLY_DELAYED_MAX)
    out["delayed"] = (~out.no_crew) & (~out.severe_delay) & (out.assembly_min > ASSEMBLY_NORMAL_MAX)
    out["normal"] = (~out.no_crew) & (~out.severe_delay) & (~out.delayed) & out.assembly_min.notna()
    out["unknown"] = out.assembly_min.isna() & ~out.no_crew

    out["hour"] = out.dispatch_time.dt.hour
    out["weekday"] = out.dispatch_time.dt.dayofweek
    out["is_weekend"] = out.weekday >= 5
    out["month"] = out.dispatch_time.dt.to_period("M").astype(str)
    return out


def detect_service_type(df):
    """Fire, EMS, or mixed -- inferred from incident-type text when present.
    Determines which NFPA 1720 turnout target applies and how the report
    frames itself. Defaults to EMS when there is nothing to go on."""
    if "incident_type" not in df.columns:
        return "ems", None
    txt = df["incident_type"].astype(str)
    fire = int(txt.str.contains(FIRE_TYPE_HINT, na=False).sum())
    ems = int(txt.str.contains(EMS_TYPE_HINT, na=False).sum())
    total = max(fire + ems, 1)
    share_fire = fire / total
    kind = "fire" if share_fire > 0.65 else "ems" if share_fire < 0.35 else "mixed"
    return kind, {"fire_calls": fire, "ems_calls": ems,
                  "fire_share": round(share_fire, 3)}


def nfpa_1720_compliance(classified, service_type):
    """Share of runs meeting the NFPA 1720 turnout target. A department can
    only claim compliance if it can measure it, and almost none can."""
    target_sec = (NFPA_1720_TURNOUT_FIRE_SEC if service_type == "fire"
                  else NFPA_1720_TURNOUT_EMS_SEC)
    known = classified[classified.assembly_min.notna()]
    if not len(known):
        return None
    within = float((known.assembly_min * 60 <= target_sec).mean())
    return {
        "target_seconds": target_sec,
        "standard": "NFPA 1720 (volunteer/mostly-volunteer deployment)",
        "applies_to": "fire response" if service_type == "fire" else "EMS response",
        "compliance_rate": round(within, 4),
        "required_rate": NFPA_COMPLIANCE_TARGET,
        "meets_standard": bool(within >= NFPA_COMPLIANCE_TARGET),
        "runs_measured": int(len(known)),
        "note": (
            f"{within:.1%} of measured runs went en route within {target_sec} seconds, against a "
            f"{NFPA_COMPLIANCE_TARGET:.0%} threshold. This target applies where a department staffs "
            "a station; a department responding from home will not meet it, and NFPA 1720 exists "
            "because that is expected. The figure is reported so the department has a documented "
            "number -- for a grant, or for an ISO Public Protection Classification review, where "
            "half the score is department quality including staffing."
        ),
    }


def analyze(df, mutual_aid_fee=350, cost_per_departure=6872, with_grid=True,
            extra_no_crew=()):
    """Aggregate the classified incidents into the audit findings."""
    from . import optimizer as opt
    df, collapse = collapse_to_incidents(df)
    disposition_review = review_dispositions(df, extra_no_crew)
    c = classify(df, extra_no_crew)
    n = len(c)
    span_days = max((c.dispatch_time.max() - c.dispatch_time.min()).days, 1)
    years = span_days / 365.25

    n_no_crew = int(c.no_crew.sum())
    n_severe = int(c.severe_delay.sum())
    n_delayed = int(c.delayed.sum())
    n_normal = int(c.normal.sum())
    n_unknown = int(c.unknown.sum())

    known = c[c.assembly_min.notna()]
    med_assembly = float(known.assembly_min.median()) if len(known) else None
    p90_assembly = float(known.assembly_min.quantile(0.90)) if len(known) else None

    # failure rate by hour and by weekday -- the schedulable structure
    by_hour = []
    for h in range(24):
        sub = c[c.hour == h]
        by_hour.append({
            "hour": h,
            "incidents": int(len(sub)),
            "failures": int(sub.no_crew.sum() + sub.severe_delay.sum()),
            "failure_rate": round(float((sub.no_crew | sub.severe_delay).mean()), 4) if len(sub) else 0.0,
            "median_assembly_min": round(float(sub.assembly_min.median()), 1)
                                    if sub.assembly_min.notna().any() else None,
        })

    weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    by_weekday = []
    for w in range(7):
        sub = c[c.weekday == w]
        by_weekday.append({
            "weekday": weekday_names[w],
            "incidents": int(len(sub)),
            "failures": int(sub.no_crew.sum() + sub.severe_delay.sum()),
            "failure_rate": round(float((sub.no_crew | sub.severe_delay).mean()), 4) if len(sub) else 0.0,
        })

    # weekday-daytime vs everything else: the specific hypothesis the rural
    # workforce literature predicts (outside employment during business hours)
    workday = c[(~c.is_weekend) & (c.hour >= 8) & (c.hour < 18)]
    offhours = c[~(((~c.is_weekend) & (c.hour >= 8) & (c.hour < 18)))]
    workday_rate = float((workday.no_crew | workday.severe_delay).mean()) if len(workday) else 0.0
    offhours_rate = float((offhours.no_crew | offhours.severe_delay).mean()) if len(offhours) else 0.0

    # worst contiguous windows (hour-of-week granularity), ranked
    windows = []
    for w in range(7):
        for h in range(24):
            sub = c[(c.weekday == w) & (c.hour == h)]
            if len(sub) < 3:
                continue
            rate = float((sub.no_crew | sub.severe_delay).mean())
            windows.append({
                "weekday": weekday_names[w], "hour": h,
                "incidents": int(len(sub)),
                "failures": int(sub.no_crew.sum() + sub.severe_delay.sum()),
                "failure_rate": round(rate, 4),
            })
    windows.sort(key=lambda r: (r["failure_rate"], r["failures"]), reverse=True)

    monthly = (c.groupby("month")
                .agg(incidents=("no_crew", "size"),
                     failures=("no_crew", lambda s: int(s.sum())))
                .reset_index().to_dict("records"))

    failures_total = n_no_crew + n_severe
    failures_per_year = failures_total / years if years > 0 else failures_total
    lo, hi = wilson_interval(failures_total, n)

    grid = opt.build_grid(c) if with_grid else None
    service_type, type_mix = detect_service_type(df)
    nfpa = nfpa_1720_compliance(c, service_type)

    return {
        "incident_collapse": collapse,
        "disposition_review": disposition_review,
        "service_type": service_type,
        "type_mix": type_mix,
        "nfpa_1720": nfpa,
        "grid": [{"weekday": v["weekday"], "hour": v["hour"], "incidents": v["incidents"],
                  "failures": v["failures"]} for v in grid.values()] if grid else None,
        "summary": {
            "incidents": n,
            "date_range": [c.dispatch_time.min().strftime("%Y-%m-%d"),
                           c.dispatch_time.max().strftime("%Y-%m-%d")],
            "span_days": span_days,
            "span_years": round(years, 2),
            "calls_per_year": round(n / years) if years > 0 else n,
            "no_crew": n_no_crew,
            "severe_delay": n_severe,
            "delayed": n_delayed,
            "normal": n_normal,
            "unknown": n_unknown,
            "failures_total": failures_total,
            "failures_per_year": round(failures_per_year, 1),
            "failure_rate": round(failures_total / n, 4) if n else 0.0,
            "failure_rate_ci95": [round(v, 4) for v in wilson_interval(failures_total, n)],
            "failures_per_year_ci95": [
                round(lo * n / years, 1) if years else 0.0,
                round(hi * n / years, 1) if years else 0.0,
            ] if n else [0.0, 0.0],
            "median_assembly_min": round(med_assembly, 1) if med_assembly is not None else None,
            "p90_assembly_min": round(p90_assembly, 1) if p90_assembly is not None else None,
        },
        "pattern": {
            "by_hour": by_hour,
            "by_weekday": by_weekday,
            "worst_windows": windows[:10],
            "workday_failure_rate": round(workday_rate, 4),
            "offhours_failure_rate": round(offhours_rate, 4),
            "workday_penalty_ratio": round(workday_rate / offhours_rate, 2)
                                      if offhours_rate > 0 else None,
            "monthly": monthly,
        },
        "cost": {
            "mutual_aid_activations_per_year": round(n_no_crew / years, 1) if years > 0 else n_no_crew,
            "mutual_aid_cost_per_year": round((n_no_crew / years) * mutual_aid_fee) if years > 0 else 0,
            "mutual_aid_fee_assumed": mutual_aid_fee,
            "basis": (
                f"Applies a real published rate (${mutual_aid_fee}/request, South County EMS) to the count "
                "of incidents where no crew formed."
            ),
        },
    }


# --- reference dataset (REAL) ---------------------------------------------

REFERENCE_PATH = "data/sf_ems_2024_real.csv"
REFERENCE_META = {
    "agency_name": "San Francisco Fire Department (real data)",
    "source": "DataSF — Fire Department and EMS Dispatched Calls for Service (dataset nuek-vuh3)",
    "source_url": "https://data.sfgov.org/Public-Safety/Fire-Department-and-Emergency-Medical-Services-Dis/nuek-vuh3",
    "rows": 40000,
    "period": "calendar year 2024",
    "filter": "unit_type = MEDIC (ambulances only)",
    "why_this_dataset": (
        "This is a REAL dispatch export, not a simulation. It is also a deliberate CONTROL: San "
        "Francisco runs career crews from continuously staffed stations, so a correct audit should "
        "find essentially no crew-assembly failure here. It does. That is the point -- the method "
        "reports what is in the data rather than manufacturing a problem, and it establishes the "
        "baseline a volunteer agency gets measured against."
    ),
    "limitation": (
        "SF is urban and career-staffed, NOT the rural volunteer service this product targets. "
        "Incident-level dispatch/en-route timestamps are published by metropolitan agencies and "
        "almost never by rural volunteer ones -- which is precisely why this product asks an agency "
        "for its own export instead of relying on public data."
    ),
}


def load_reference_export():
    """The bundled real San Francisco dispatch export, read from disk.
    No network access at runtime."""
    with open(REFERENCE_PATH, "rb") as fh:
        return fh.read()


CSV_TEMPLATE_COLUMNS = [
    ("Incident Number", "any unique id per call", "recommended"),
    ("Unit Notified By Dispatch", "tone-out / dispatch timestamp", "REQUIRED"),
    ("Unit En Route", "when the crew went responding (blank if never)", "strongly recommended"),
    ("Unit Arrived On Scene", "arrival timestamp", "optional"),
    ("Unit Back In Service", "clear timestamp", "optional"),
    ("Unit Call Sign", "which unit responded", "optional"),
    ("Disposition", "outcome / cancel reason text", "recommended"),
    ("Mutual Aid", "Y/N — was aid requested from another agency", "recommended"),
]
