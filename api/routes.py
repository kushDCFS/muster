import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Request

from model import real_data as rd
from model import impact as impact_model

router = APIRouter(prefix="/api")

GAP_PERCENTILE = 85  # an hour counts as an "elevated-risk window" if it's in
                     # this county's own top 15% of hourly gap probability
                     # across the forecast horizon. Absolute gap probabilities
                     # are small by construction at rural call volumes; what
                     # the product flags is relative elevation, not a fixed cutoff.
MEANINGFUL_RISK_FLOOR = 0.005  # below this, a county's hourly gap probability is
                     # flat/near-zero everywhere (e.g. Placer's 24 modeled
                     # ambulances) and a RELATIVE top-15% threshold degenerates
                     # into flagging noise (sometimes literally the whole day),
                     # not a real signal -- suppress window-flagging entirely.

# Per-enrolled-resident annual savings range from REAL published community
# paramedicine / prevention outreach programs -- NOT measured for any county
# in this dataset. Low end ~ Nova Scotia rural CP program (per-person annual
# cost $2,380 -> $1,375, i.e. ~$1,005/yr); high end ~ a Washington MIH-CP
# program's realized net savings (~$1,545-1,900/member/yr after program
# cost, in the range of the broader "avoided ED visit" literature, ~$1,900/
# visit). Applied to `target_count` as an illustrative range, not a
# prediction: it assumes similar enrollment and outcomes to those published
# programs, which have not been tested on this county's actual population.
PREVENTION_SAVINGS_PER_PERSON_LOW = 1000
PREVENTION_SAVINGS_PER_PERSON_HIGH = 1900

# Real, publicly reported annual call-volume figures for counties in this
# dataset, found via public search -- NOT part of the model fit, used only to
# spot-check the modeled annual_calls_est against whatever ground truth is
# publicly available. Four counties found; the rest have no public per-county
# figure, which is exactly the gap a real pilot-county data-sharing agreement
# would close (see FINDINGS.md).
REAL_WORLD_CHECKS = [
    {
        "county_name": "Tuolumne County",
        "real_calls": 8000,
        "real_year": "current (ongoing)",
        "source": "Tuolumne County Ambulance Service, official county page",
        "source_url": "https://tuolumnecounty.ca.gov/299/Ambulance",
    },
    {
        "county_name": "Lassen County",
        "real_calls": 3354,
        "real_year": "2025",
        "source": "SEMSA annual report to the Lassen County Emergency Medical Care Committee",
        "source_url": "https://www.sierradailynews.com/local/semsa-ends-lassen-county-ambulance-service-plans-emergency-medical-care-committee-seeks-new-provider/",
    },
    {
        "county_name": "Del Norte County",
        "real_calls": 4546,
        "real_year": "2024",
        "source": "EndPoint EMS Consulting assessment, reported by Redwood Voice",
        "source_url": "https://www.redwoodvoice.org/analysis-finds-del-norte-ambulance-meeting-minimal-requirements-though-response-time-to-klamath-gasquet-can-exceed-30-minutes/",
    },
    {
        "county_name": "Mono County",
        "real_calls": 1710,
        "real_year": "FY2020 (pre-recent growth; likely a floor, not a current estimate)",
        "source": "Mono County EMS budget report (OpenGov)",
        "source_url": "https://stories.opengov.com/monocountyca/published/FEFb1xc5F",
    },
]


def _fitted(request: Request):
    return request.app.state.fitted


def _overrides(num_ambulances, roster_size, annual_calls, transport_min):
    """Real-agency values substituted for the prototype's modeled estimates.
    Any subset may be supplied; omitted ones fall back to the model."""
    ov = {}
    if num_ambulances is not None and num_ambulances > 0:
        ov["num_ambulances"] = int(num_ambulances)
    if roster_size is not None and roster_size > 0:
        ov["roster_size"] = float(roster_size)
    if annual_calls is not None and annual_calls > 0:
        ov["annual_calls"] = float(annual_calls)
    if transport_min is not None and transport_min > 0:
        ov["transport_min"] = float(transport_min)
    return ov


def _county_or_404(f, county_id: int):
    try:
        return f.county_row(county_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown county_id {county_id}")


@router.get("/counties")
def list_counties(request: Request):
    f = _fitted(request)
    out = []
    for _, co in f.counties.iterrows():
        partner = f.mutual_aid_partners[int(co.county_id)]
        out.append({
            "county_id": int(co.county_id),
            "name": co["name"],
            "population": int(co.population),
            "rurality_tier": co.rurality_tier,
            "density_per_sqmi": float(co.density_per_sqmi),
            "num_ambulances": int(co.num_ambulances),
            "ambulance_basis": co.ambulance_basis,
            "roster_size_est": int(co.roster_size_est),
            "annual_calls_est": round(float(co.annual_calls_est), 0),
            "ems_response_rate_per_1000": float(co.ems_response_rate_per_1000),
            "rate_basis": co.rate_basis,
            "mutual_aid_partner": partner["partner_name"],
            "mutual_aid_distance_mi": partner["distance_mi"],
            "mutual_aid_same_lemsa": partner["same_lemsa"],
        })
    return {"real_county_data": True, "counties": out}


@router.get("/counties/{county_id}/forecast")
def forecast(county_id: int, request: Request, days: int = 7,
             num_ambulances: int = None, roster_size: float = None,
             annual_calls: float = None, transport_min: float = None):
    f = _fitted(request)
    co = _county_or_404(f, county_id)
    days = max(1, min(days, f.forecast_days))
    ov = _overrides(num_ambulances, roster_size, annual_calls, transport_min)

    out_days = []
    for offset in range(1, days + 1):
        day_idx = f.train_days + offset - 1
        date = f.dates.iloc[day_idx]
        risk = f.daily_risk(county_id, day_idx, **ov)
        hourly, _ = f.hourly_gap(county_id, day_idx, **ov)
        drivers = f.drivers(county_id, day_idx)
        out_days.append({
            "date": date.strftime("%Y-%m-%d"),
            "weekday": date.strftime("%a"),
            **risk,
            "drivers": drivers,
            "hourly": hourly,
        })

    return {
        "county_id": county_id,
        "county_name": co["name"],
        "num_ambulances": int(co.num_ambulances),
        "forecast_start": out_days[0]["date"] if out_days else None,
        "forecast_is_real_weather": True,
        "overrides_applied": ov,
        "days": out_days,
    }


@router.get("/counties/{county_id}/gaps")
def gaps(county_id: int, request: Request, days: int = 14,
         num_ambulances: int = None, roster_size: float = None,
         annual_calls: float = None, transport_min: float = None):
    f = _fitted(request)
    _county_or_404(f, county_id)
    days = max(1, min(days, f.forecast_days))
    ov = _overrides(num_ambulances, roster_size, annual_calls, transport_min)

    horizon = f.horizon_hours(county_id, days, **ov)
    peak = max(h["gap_probability"] for _, h in horizon)
    if peak < MEANINGFUL_RISK_FLOOR:
        return {"county_id": county_id, "threshold": None, "windows": [],
                "note": "Peak gap probability across the forecast horizon never exceeds "
                        f"{MEANINGFUL_RISK_FLOOR:.1%} -- no window is meaningfully elevated."}
    threshold = float(np.percentile([h["gap_probability"] for _, h in horizon], GAP_PERCENTILE))

    windows = []
    by_date = {}
    for date, h in horizon:
        by_date.setdefault(date, []).append(h)

    date0 = f.dates.iloc[0]
    for date, hourly in by_date.items():
        drivers = f.drivers(county_id, int((date - date0).days), top_n=1)
        reason = drivers[0]["label"] if drivers else None
        run = []
        for h in hourly:
            if h["gap_probability"] >= threshold:
                run.append(h)
            else:
                if run:
                    windows.append(_make_window(date, run, reason))
                    run = []
        if run:
            windows.append(_make_window(date, run, reason))

    windows.sort(key=lambda w: w["gap_probability"], reverse=True)
    return {"county_id": county_id, "threshold": round(threshold, 4),
            "windows": windows[:15]}


def _make_window(date, hours, reason):
    probs = [h["gap_probability"] for h in hours]
    los = [h["gap_lo"] for h in hours]
    his = [h["gap_hi"] for h in hours]
    return {
        "date": date.strftime("%Y-%m-%d"),
        "weekday": date.strftime("%a"),
        "start_hour": hours[0]["hour"],
        "end_hour": hours[-1]["hour"] + 1,
        "gap_probability": round(float(np.mean(probs)), 4),
        "gap_lo": round(float(np.mean(los)), 4),
        "gap_hi": round(float(np.mean(his)), 4),
        "reason": reason,
    }


@router.get("/counties/{county_id}/actions")
def actions(county_id: int, request: Request,
            num_ambulances: int = None, roster_size: float = None,
            annual_calls: float = None, transport_min: float = None):
    f = _fitted(request)
    co = _county_or_404(f, county_id)
    ov = _overrides(num_ambulances, roster_size, annual_calls, transport_min)
    n_amb = ov.get("num_ambulances", int(co.num_ambulances))
    n_roster = ov.get("roster_size", float(co.roster_size_est))
    date0 = f.dates.iloc[0]

    # scan the forecast horizon once for the worst windows, relative to this
    # county's own distribution (see GAP_PERCENTILE)
    horizon = f.horizon_hours(county_id, f.forecast_days, **ov)
    peak = max(h["gap_probability"] for _, h in horizon)
    by_date = {}
    for date, h in horizon:
        by_date.setdefault(date, []).append(h)

    all_windows = []
    threshold = float(np.percentile([h["gap_probability"] for _, h in horizon], GAP_PERCENTILE)) \
        if peak >= MEANINGFUL_RISK_FLOOR else float("inf")
    for date, hourly in by_date.items():
        drivers = f.drivers(county_id, int((date - date0).days), top_n=1)
        reason = drivers[0]["label"] if drivers else "seasonal baseline"
        run = []
        for h in hourly:
            if h["gap_probability"] >= threshold:
                run.append(h)
            else:
                if run:
                    all_windows.append((date, run, reason))
                    run = []
        if run:
            all_windows.append((date, run, reason))

    all_windows.sort(key=lambda w: np.mean([h["gap_probability"] for h in w[1]]), reverse=True)

    cards = {}

    # 1. staff this shift: worst window. Modeled effect is boosting the
    # RESPONSIVE roster for this specific window -- e.g. specifically
    # tasking 2-3 additional on-call volunteers to be reachable -- not
    # adding a truck. This matches the mechanism the rural EMS literature
    # documents as binding (crew assembly, not simultaneous-call collision;
    # see model/availability.py).
    if all_windows:
        date, hrs, reason = all_windows[0]
        day_idx_lookup = int((date - date0).days)
        before = float(np.mean([h["gap_probability"] for h in hrs]))
        hourly_boost, _ = f.hourly_gap(county_id, day_idx_lookup, **{**ov, "roster_size": n_roster * 2})
        after_vals = [hourly_boost[h["hour"]]["gap_probability"] for h in hrs]
        after = float(np.mean(after_vals))
        cards["staff_shift"] = {
            "window": f"{date.strftime('%a %b %d')} {hrs[0]['hour']:02d}:00-{hrs[-1]['hour']+1:02d}:00",
            "reason": f"driven mainly by {reason}",
            "gap_probability": round(before, 3),
            "before": round(before, 4),
            "after": round(after, 4),
            "reduction_pct": round(100 * (before - after) / before, 1) if before > 0 else 0,
            "predicted_effect": f"specifically tasking extra on-call volunteers for this window "
                                 f"(not adding a truck) drops predicted uncovered-call probability "
                                 f"from {before:.2%} to about {after:.2%}",
        }
    else:
        cards["staff_shift"] = None

    # 2. mutual aid: second-worst window on a DIFFERENT date, framed as
    # coordination. Modeled effect is borrowing a neighboring agency's
    # truck AND its own (presumably better-staffed, pre-committed) crew.
    mutual = next((w for w in all_windows[1:] if w[0] != all_windows[0][0]), None) if all_windows else None
    partner = f.mutual_aid_partners[county_id]
    if mutual:
        date, hrs, reason = mutual
        before = float(np.mean([h["gap_probability"] for h in hrs]))
        day_idx_lookup = int((date - date0).days)
        hourly_boost, _ = f.hourly_gap(
            county_id, day_idx_lookup,
            **{**ov, "num_ambulances": n_amb + 1, "roster_size": n_roster * 2}
        )
        after = float(np.mean([hourly_boost[h["hour"]]["gap_probability"] for h in hrs]))
        lemsa_note = ("already in the same regional EMS agency" if partner["same_lemsa"]
                      else "not confirmed to be in the same regional EMS agency, but the closest real option")
        cards["mutual_aid"] = {
            "window": f"{date.strftime('%a %b %d')} {hrs[0]['hour']:02d}:00-{hrs[-1]['hour']+1:02d}:00",
            "reason": f"driven mainly by {reason}; own capacity likely insufficient",
            "gap_probability": round(before, 3),
            "before": round(before, 4),
            "after": round(after, 4),
            "reduction_pct": round(100 * (before - after) / before, 1) if before > 0 else 0,
            "partner_county": partner["partner_name"],
            "partner_distance_mi": partner["distance_mi"],
            "partner_same_lemsa": partner["same_lemsa"],
            "predicted_effect": f"pre-arranging backup from {partner['partner_name']} "
                                 f"({partner['distance_mi']:.0f} mi away, {lemsa_note}) for this window could "
                                 f"bring predicted uncovered-call probability from {before:.2%} to about {after:.2%}",
        }
    else:
        cards["mutual_aid"] = None

    # 3. prevention: top-risk block groups, counts and tiers only
    blocks = f.prevention[f.prevention.county_id == county_id].copy()
    blocks["tier"] = blocks.risk_score.apply(f.risk_tier)
    top = blocks.sort_values("risk_score", ascending=False).head(3)
    total_high_acuity = int(top.high_acuity_count.sum())
    savings_low = total_high_acuity * PREVENTION_SAVINGS_PER_PERSON_LOW
    savings_high = total_high_acuity * PREVENTION_SAVINGS_PER_PERSON_HIGH
    cards["prevention"] = {
        "window": "this month",
        "reason": "highest chronic-risk block groups in the county (age + chronic-condition prevalence)",
        "target_count": total_high_acuity,
        "block_groups": [
            {"block_group": r.block_group, "tier": r.tier, "high_acuity_count": int(r.high_acuity_count)}
            for r in top.itertuples()
        ],
        "estimated_annual_savings_low": savings_low,
        "estimated_annual_savings_high": savings_high,
        "predicted_effect": f"routine check-ins for these ~{total_high_acuity} higher-risk residents "
                             f"are the modeled lever most likely to reduce avoidable 911 calls",
    }

    # Annualized event count: sum of gap_probability IS P(call arrives AND
    # uncovered) for that hour, so summing across a representative year's
    # hours is a direct expectation, not a new assumption. Baseline vs. the
    # mutual-aid intervention standing year-round, not just for one window.
    annual_now = f.annualized_uncovered_calls(county_id, **ov)
    annual_with_mutual_aid = f.annualized_uncovered_calls(
        county_id, **{**ov, "num_ambulances": n_amb + 1, "roster_size": n_roster * 2}
    )

    impact = {
        "elevated_windows_14d": len(all_windows),
        "worst_window_before": cards["staff_shift"]["before"] if cards["staff_shift"] else None,
        "worst_window_after": cards["staff_shift"]["after"] if cards["staff_shift"] else None,
        "best_reduction_pct": cards["staff_shift"]["reduction_pct"] if cards["staff_shift"] else None,
        "residents_flagged": total_high_acuity,
        "annual_uncovered_calls_now": annual_now,
        "annual_uncovered_calls_with_mutual_aid": annual_with_mutual_aid,
    }

    return {"county_id": county_id, "county_name": co["name"], "actions": cards,
            "impact": impact, "overrides_applied": ov}


@router.get("/counties/{county_id}/impact")
def impact_detail(county_id: int, request: Request,
                  num_ambulances: int = None, roster_size: float = None,
                  annual_calls: float = None, transport_min: float = None):
    """Quantified impact across four channels, each with an explicit basis
    string naming the real published source and what remains an estimate."""
    f = _fitted(request)
    co = _county_or_404(f, county_id)
    ov = _overrides(num_ambulances, roster_size, annual_calls, transport_min)
    n_amb = ov.get("num_ambulances", int(co.num_ambulances))
    n_roster = ov.get("roster_size", float(co.roster_size_est))
    n_calls = ov.get("annual_calls", float(co.annual_calls_est))

    now = f.annualized_uncovered_calls(county_id, sample_every=14, **ov)
    after = f.annualized_uncovered_calls(
        county_id, sample_every=14, **{**ov, "num_ambulances": n_amb + 1, "roster_size": n_roster * 2}
    )

    blocks = f.prevention[f.prevention.county_id == county_id]
    residents = int(blocks.sort_values("risk_score", ascending=False).head(3).high_acuity_count.sum())

    cov = impact_model.coverage_impact(now, after, n_calls)
    prev = impact_model.prevention_impact(residents)
    work = impact_model.workforce_impact(n_roster)
    econ = impact_model.transport_economics(n_calls)

    total_low = prev["savings_low"] + work["retention_savings_low"]
    total_high = prev["savings_high"] + work["retention_savings_high"]

    return {
        "county_id": county_id,
        "county_name": co["name"],
        "overrides_applied": ov,
        "coverage": cov,
        "prevention": prev,
        "workforce": work,
        "transport_economics": econ,
        "combined_annual_savings_low": total_low,
        "combined_annual_savings_high": total_high,
        "combined_basis": (
            "Sum of the two channels that represent money not spent (prevention + retention). "
            "Coverage improvements are reported as calls moved out of an uncovered hour, NOT "
            "converted to dollars -- doing so would require a response-time-to-outcome link the "
            "literature does not support. No lives-saved or Value-of-Statistical-Life figure is "
            "computed anywhere in this tool, deliberately."
        ),
    }


@router.get("/usecases")
def usecases(request: Request):
    """All counties ranked by modeled annual exposure and how much of it a
    standing mutual-aid arrangement would cover. Computed in one request
    (rather than 19 client-side calls) because the annualized figure sweeps
    a multi-year weather history per county. Cached after first build --
    nothing here depends on user input."""
    f = _fitted(request)
    if getattr(f, "_usecase_cache", None) is not None:
        return f._usecase_cache
    out = build_usecases(f)
    f._usecase_cache = out
    return out


def build_usecases(f):
    """Extracted so app startup can warm this cache -- a live demo shouldn't
    pay a 10-15s wait the first time the Use cases tab is opened."""
    rows = []
    for _, co in f.counties.iterrows():
        cid = int(co.county_id)
        n_amb, n_roster = int(co.num_ambulances), float(co.roster_size_est)
        now = f.annualized_uncovered_calls(cid, sample_every=14)
        after = f.annualized_uncovered_calls(
            cid, sample_every=14, num_ambulances=n_amb + 1, roster_size=n_roster * 2
        )
        partner = f.mutual_aid_partners[cid]
        rows.append({
            "county_id": cid,
            "name": co["name"],
            "population": int(co.population),
            "num_ambulances": n_amb,
            "roster_size_est": int(n_roster),
            "annual_calls_est": round(float(co.annual_calls_est), 0),
            "uncovered_now": now,
            "uncovered_with_mutual_aid": after,
            "avoided": round(now - after, 1),
            "mutual_aid_partner": partner["partner_name"],
            "mutual_aid_distance_mi": partner["distance_mi"],
        })
    rows.sort(key=lambda r: -r["avoided"])
    return {
        "note": "Modeled estimates, not observed outcomes. 'Avoided' is the difference between a "
                "county's current modeled exposure and the same county with a standing mutual-aid "
                "arrangement in place year-round.",
        "counties": rows,
    }


@router.get("/validation")
def validation(request: Request, county_id: int = 0):
    f = _fitted(request)
    v = f.validation

    overall = {
        "dev_none": round(float(v.dev_none.mean()), 4),
        "dev_full": round(float(v.dev_full.mean()), 4),
        "dev_partial": round(float(v.dev_partial.mean()), 4),
        "lift_none": round(float(v.lift_none.mean()), 3),
        "lift_full": round(float(v.lift_full.mean()), 3),
        "lift_partial": round(float(v.lift_partial.mean()), 3),
        "gain_vs_none_pct": round(100 * (v.dev_none.mean() - v.dev_partial.mean()) / v.dev_none.mean(), 2),
        "gain_vs_full_pct": round(100 * (v.dev_full.mean() - v.dev_partial.mean()) / v.dev_full.mean(), 2),
    }

    strata_df = v.copy()
    strata_df["bin"] = pd.qcut(strata_df.annual_calls, min(5, strata_df.county_id.nunique()), labels=False, duplicates="drop")
    strata = strata_df.groupby("bin").agg(
        median_calls=("annual_calls", "median"),
        n=("county_id", "size"),
        dev_none=("dev_none", "mean"),
        dev_full=("dev_full", "mean"),
        dev_partial=("dev_partial", "mean"),
        lift_none=("lift_none", "mean"),
        lift_partial=("lift_partial", "mean"),
        shrink=("mean_shrink_w", "mean"),
    ).reset_index()
    strata["gain_vs_none_pct"] = 100 * (strata.dev_none - strata.dev_partial) / strata.dev_none
    strata_records = strata.round(4).to_dict(orient="records")

    if county_id not in f.counties.county_id.values:
        county_id = int(f.counties.county_id.iloc[0])
    weights = f.fit["shrink_weight"][county_id]
    shrinkage = [{"term": t, "weight": round(float(w), 3)} for t, w in zip(rd.TERMS, weights)]

    sens = pd.read_csv("data/sensitivity.csv")
    sensitivity = sens.round(3).to_dict(orient="records")

    # Sierra County is a REAL county in this dataset (near Granite Bay, CA) --
    # no "closest analog" needed anymore, it's directly in the 19-county list.
    sierra = f.counties.loc[f.counties["name"] == "Sierra County"].iloc[0]
    contrast = f.counties.sort_values("population", ascending=False).iloc[0]
    nearest_volume_bucket = min(sensitivity, key=lambda r: abs(r["volume"] - sierra.annual_calls_est))["volume"]

    calibration = []
    for chk in REAL_WORLD_CHECKS:
        row = f.counties.loc[f.counties["name"] == chk["county_name"]]
        if row.empty:
            continue
        model_est = float(row.iloc[0].annual_calls_est)
        gap_pct = round(100 * (model_est - chk["real_calls"]) / chk["real_calls"], 1)
        calibration.append({
            **chk,
            "county_id": int(row.iloc[0].county_id),
            "model_estimate": round(model_est, 0),
            "model_vs_real_pct": gap_pct,  # negative = model runs low
        })
    calibration_note = (
        "Every real per-county call-volume figure we could find publicly, compared against this "
        "county's modeled estimate. The model runs consistently low here (by 13-41%), which is a "
        "real finding, not something we hid: a single statewide/regional per-capita rate misses real "
        "county-to-county variation, which is exactly the gap partial pooling with real per-county "
        "history would close. Only 4 of 19 counties have a public figure to check against -- the rest "
        "would need a real data-sharing agreement."
    )

    return {
        "note": "Held out on the most recent 1 year of REAL weather history (real dates, real "
                "temperature/precipitation/snowfall at each county's centroid); see FINDINGS.md for "
                "why partial pooling roughly ties county-only estimation at these volumes and beats "
                "complete pooling — the honest claim is that it never loses much and protects against "
                "naive lumping.",
        "overall": overall,
        "strata": strata_records,
        "shrinkage_for_county": {"county_id": county_id, "coefficients": shrinkage},
        "sensitivity_sweep": sensitivity,
        "local_reference": {
            "name": "Sierra County, CA",
            "note": "Population, land area, and weather calendar are real for this county (see "
                    "data/real_counties.csv). Its estimated annual call volume is real population x "
                    "the real CY2020 statewide CEMSIS response rate — an estimate, not an observed count.",
            "county_id": int(sierra.county_id),
            "population": int(sierra.population),
            "annual_calls_est": round(float(sierra.annual_calls_est), 0),
            "sensitivity_volume_row": nearest_volume_bucket,
            "high_volume_contrast": {
                "county_id": int(contrast.county_id), "name": contrast["name"],
                "population": int(contrast.population),
                "annual_calls_est": round(float(contrast.annual_calls_est), 0),
            },
        },
        "real_world_calibration": {
            "note": calibration_note,
            "checks": calibration,
        },
    }
