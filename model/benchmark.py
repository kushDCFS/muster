"""
Cross-agency benchmarking: the part of this product that compounds.

Crew-assembly reliability does not exist as a published dataset. Nobody --
not NFPA, not a state fire marshal, not an insurer -- can currently tell a
volunteer department how its turnout compares to its peers, because no one
collects it. Every audit this tool runs adds one row to the only such series
in existence, which makes the next audit more valuable than the last.

That is the moat, and it is also the second product: the benchmark is worth
more to a state office or an insurer than the individual report is to the
agency that generated it.

Cohorts are matched on service type and call volume, because a 300-call
volunteer department and a 6,000-call combination department are not peers
and comparing them would produce a number that misleads in both directions.
"""

import json
import os
from datetime import datetime, timezone

COHORT_PATH = "data/benchmark_cohort.json"

# Volume bands. A department only gets compared against agencies running a
# broadly similar number of calls -- turnout expectations differ enormously
# between a service toned 200 times a year and one toned 5,000 times.
BANDS = [
    (0, 500, "under 500 calls/yr"),
    (500, 1500, "500–1,500 calls/yr"),
    (1500, 4000, "1,500–4,000 calls/yr"),
    (4000, 10_000, "4,000–10,000 calls/yr"),
    (10_000, 10**9, "over 10,000 calls/yr"),
]


def band_for(calls_per_year):
    for lo, hi, label in BANDS:
        if lo <= calls_per_year < hi:
            return label
    return BANDS[-1][2]


def _load():
    if not os.path.exists(COHORT_PATH):
        return []
    try:
        with open(COHORT_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


def _save(rows):
    os.makedirs(os.path.dirname(COHORT_PATH), exist_ok=True)
    with open(COHORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)


def contribute(findings, service_type="ems", label=None):
    """Add one de-identified row to the cohort.

    Only four aggregate numbers are stored -- no agency name, no location, no
    incident, and nothing that could identify a person. A row is a shape, not
    a record."""
    s = findings["summary"]
    row = {
        "service_type": service_type,
        "band": band_for(s["calls_per_year"]),
        "calls_per_year": s["calls_per_year"],
        "failure_rate": s["failure_rate"],
        "median_assembly_min": s["median_assembly_min"],
        "workday_penalty": findings["pattern"].get("workday_penalty_ratio"),
        "added": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "label": label,
    }
    rows = _load()
    rows.append(row)
    _save(rows)
    return row


def percentile_of(value, values, lower_is_better=True):
    """Share of the cohort this value beats."""
    if not values:
        return None
    if lower_is_better:
        better = sum(1 for v in values if value < v)
    else:
        better = sum(1 for v in values if value > v)
    return round(100 * better / len(values))


def compare(findings, service_type="ems"):
    """Where this agency sits against its cohort. Returns None when the
    cohort is too small to say anything honest -- a percentile computed from
    three agencies is noise dressed as insight."""
    s = findings["summary"]
    band = band_for(s["calls_per_year"])
    rows = [r for r in _load()
            if r["service_type"] == service_type and r["band"] == band]

    MIN_COHORT = 8
    if len(rows) < MIN_COHORT:
        return {
            "available": False,
            "cohort_size": len(rows),
            "min_required": MIN_COHORT,
            "band": band,
            "service_type": service_type,
            "note": (
                f"Only {len(rows)} comparable {service_type.upper()} agencies in the "
                f"{band} cohort so far; {MIN_COHORT} are needed before a percentile means "
                "anything. This is the dataset being built — every audit adds one row, and "
                "no agency identity is stored."
            ),
        }

    fr = [r["failure_rate"] for r in rows]
    ma = [r["median_assembly_min"] for r in rows if r["median_assembly_min"] is not None]
    return {
        "available": True,
        "cohort_size": len(rows),
        "band": band,
        "service_type": service_type,
        "failure_rate_percentile": percentile_of(s["failure_rate"], fr),
        "cohort_median_failure_rate": round(sorted(fr)[len(fr) // 2], 4),
        "assembly_percentile": percentile_of(s["median_assembly_min"], ma)
                                if s["median_assembly_min"] is not None else None,
        "cohort_median_assembly": round(sorted(ma)[len(ma) // 2], 1) if ma else None,
        "note": (
            f"Compared against {len(rows)} de-identified {service_type.upper()} agencies in the "
            f"{band} band. Cohort rows contain four aggregate numbers each and no agency identity."
        ),
    }
