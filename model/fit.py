"""
Orchestration: load the real county/weather data, fit the partial-pooling
model once, and expose everything the API needs as an in-memory `Fitted`
object.

No database. Everything here is recomputed at process startup (~15-30s,
mostly the 19 per-county GLM fits) and cached in memory for the life of the
process — fine for a prototype; a real deployment would persist the fit and
refresh it on a schedule as new weather/call data arrives.
"""

import numpy as np
import pandas as pd

from . import real_data as rd
from . import temporal
from . import availability

Z_80 = 1.2816  # standard-normal quantile for an 80% CI (10th/90th percentile)

# The partial-pooling model is fit only within this product's actual target
# cohort -- rural, 1-3 ambulance counties. Nevada/Yolo/Placer are ~300x larger
# in call volume than Alpine (this dataset spans real counties from 1,099 to
# 433,822 residents); pooling a hierarchical model across that much scale
# imbalance is not a meaningful "shared coefficient" exercise and produces
# a shared fit dominated by the largest counties. Those three are kept in the
# app purely as an out-of-cohort volume contrast, each fit with its own
# standalone GLM (no shrinkage attempted), consistent with the product
# thesis that pooling only makes sense across a genuinely comparable cohort.
POOLING_POP_CEILING = 60_000


class Fitted:
    def __init__(self):
        data = rd.generate_all()
        self.counties = data["counties"]
        self.calendars = data["calendars"]          # {county_id: DataFrame w/ TERMS + date}
        self.dates = data["dates"]                    # shared date Series, index = day_idx
        self.counts = data["counts"]                  # {county_id: array of daily counts}
        self.durations = data["durations"]
        self.prevention = data["prevention"]
        self.mutual_aid_partners = rd.compute_mutual_aid_partners(self.counties)

        self.county_ids = sorted(self.counties.county_id.tolist())
        self.pooled_ids = sorted(self.counties.loc[
            self.counties.population < POOLING_POP_CEILING, "county_id"].tolist())
        self.contrast_ids = [c for c in self.county_ids if c not in self.pooled_ids]

        self.train_days = data["train_days"]
        self.forecast_days = data["forecast_days"]
        self.last_train_date = self.dates.iloc[self.train_days - 1]

        pooled_fit = temporal.fit_all(self.calendars, self.counts, self.pooled_ids, self.train_days)
        self.validation = temporal.held_out_validation(
            self.calendars, self.counts, self.pooled_ids, self.train_days
        )
        self.fit = self._merge_contrast_counties(pooled_fit)

        self.duration_models = {
            int(cid): availability.fit_duration_model(self.durations, cid)
            for cid in self.counties.county_id
        }

        # county-level risk-score quantile across ALL prevention block groups,
        # used to assign risk tiers (Low/Medium/High) consistently statewide
        self._risk_q = self.prevention.risk_score.quantile([0.5, 0.85]).values

    # ---- helpers -----------------------------------------------------

    def _merge_contrast_counties(self, pooled_fit):
        """Scatter the pooled-cohort fit into full-size (n_counties,) arrays
        indexed by county_id, and fill in the out-of-cohort contrast counties
        with their own standalone GLM (no shrinkage -- shared == own, weight
        == 1), since they were never part of the pooling exercise."""
        n_terms = len(rd.TERMS)
        n_total = len(self.county_ids)
        out = {
            "own_intercept": np.zeros(n_total), "own_beta": np.zeros((n_total, n_terms)),
            "own_se": np.zeros((n_total, n_terms)), "shared_intercept": np.zeros(n_total),
            "shared_beta": pooled_fit["shared_beta"],
            "shrunk_beta": np.zeros((n_total, n_terms)),
            "shrunk_intercept": np.zeros(n_total),
            "shrink_weight": np.zeros((n_total, n_terms)),
            "shrunk_post_var": np.zeros((n_total, n_terms)),
        }
        for i, cid in enumerate(self.pooled_ids):
            out["own_intercept"][cid] = pooled_fit["own_intercept"][i]
            out["own_beta"][cid] = pooled_fit["own_beta"][i]
            out["own_se"][cid] = pooled_fit["own_se"][i]
            out["shared_intercept"][cid] = pooled_fit["shared_intercept"][i]
            out["shrunk_beta"][cid] = pooled_fit["shrunk_beta"][i]
            out["shrunk_intercept"][cid] = pooled_fit["shrunk_intercept"][i]
            out["shrink_weight"][cid] = pooled_fit["shrink_weight"][i]
            out["shrunk_post_var"][cid] = pooled_fit["shrunk_post_var"][i]

        for cid in self.contrast_ids:
            Z = self.calendars[cid][rd.TERMS].values[: self.train_days]
            p, se = temporal.fit_county(Z, self.counts[cid])
            out["own_intercept"][cid] = p[0]
            out["own_beta"][cid] = p[1:]
            out["own_se"][cid] = se[1:]
            out["shared_intercept"][cid] = p[0]      # no pooling for these -- own IS shared
            out["shrunk_beta"][cid] = p[1:]
            out["shrunk_intercept"][cid] = p[0]
            out["shrink_weight"][cid] = 1.0
            out["shrunk_post_var"][cid] = se[1:] ** 2
        return out

    def county_row(self, county_id):
        row = self.counties.loc[self.counties.county_id == county_id]
        if row.empty:
            raise KeyError(county_id)
        return row.iloc[0]

    def risk_tier(self, score):
        if score >= self._risk_q[1]:
            return "High"
        if score >= self._risk_q[0]:
            return "Medium"
        return "Low"

    def daily_mu_ci(self, county_id, day_idx, annual_calls=None):
        """Expected calls (mid, lo, hi) for one calendar day index, from the
        shrunk coefficients and their approximate posterior variance.

        `annual_calls` lets an agency substitute their REAL annual call
        volume for our modeled estimate. It rescales the level of the curve
        proportionally while keeping the fitted temporal SHAPE (seasonality,
        weekend, heat, storm) intact -- the shape is what the model learned
        and what a county typically can't supply from a single headline
        number."""
        Z = self.calendars[county_id].loc[day_idx, rd.TERMS].values.astype(float)
        alpha = self.fit["shrunk_intercept"][county_id]
        beta = self.fit["shrunk_beta"][county_id]
        pv = self.fit["shrunk_post_var"][county_id]

        eta = alpha + Z @ beta
        var_eta = float(np.sum((Z ** 2) * pv))
        se_eta = np.sqrt(max(var_eta, 1e-9))

        mu = float(np.exp(eta))
        mu_lo = float(np.exp(eta - Z_80 * se_eta))
        mu_hi = float(np.exp(eta + Z_80 * se_eta))

        if annual_calls is not None and annual_calls > 0:
            modeled_annual = float(self.county_row(county_id).annual_calls_est)
            if modeled_annual > 0:
                scale = float(annual_calls) / modeled_annual
                mu, mu_lo, mu_hi = mu * scale, mu_lo * scale, mu_hi * scale
        return mu, mu_lo, mu_hi

    def drivers(self, county_id, day_idx, top_n=3):
        Z = self.calendars[county_id].loc[day_idx, rd.TERMS].values.astype(float)
        beta = self.fit["shrunk_beta"][county_id]
        contribs = []
        for j, term in enumerate(rd.TERMS):
            log_contrib = beta[j] * Z[j]
            if abs(Z[j]) < 1e-9:
                continue
            contribs.append({
                "term": term,
                "label": rd.TERM_LABELS[term],
                "pct_change": round((np.exp(log_contrib) - 1) * 100, 1),
                "log_contrib": log_contrib,
            })
        contribs.sort(key=lambda r: abs(r["log_contrib"]), reverse=True)
        return [{"term": c["term"], "label": c["label"], "pct_change": c["pct_change"]}
                for c in contribs[:top_n]]

    def hourly_gap(self, county_id, day_idx, num_ambulances=None, roster_size=None,
                    annual_calls=None, transport_min=None):
        """Per-hour P(a 911 call goes uncovered), with an 80% CI band.

        Combines two mechanisms (see model/availability.py): the truck
        already being out on another call (Erlang-C, usually minor in a
        rural, low-utilization county), and a crew failing to assemble at
        all -- the mechanism the rural EMS literature actually documents as
        binding, driven by time-of-day/weekday vs. roster size. Both must
        NOT happen for the call to be covered, so P(uncovered) = 1 -
        P(truck free) x P(crew assembles).

        Every one of `num_ambulances`, `roster_size`, `annual_calls`, and
        `transport_min` is a quantity this prototype currently MODELS but a
        real agency actually knows -- passing them substitutes real data for
        our estimate (see the "Use your real numbers" panel)."""
        co = self.county_row(county_id)
        c_amb = num_ambulances if num_ambulances is not None else int(co.num_ambulances)
        c_roster = roster_size if roster_size is not None else float(co.roster_size_est)
        dm = self.duration_models[county_id]
        if transport_min is not None and transport_min > 0:
            # keep the fitted relative uncertainty, rescale the level
            rel_se = dm["se_mean_min"] / dm["mean_min"] if dm["mean_min"] > 0 else 0.1
            dm = {"mean_min": float(transport_min), "se_mean_min": float(transport_min) * rel_se}
        is_weekend = bool(self.calendars[county_id].loc[day_idx, "weekend"])

        mu, mu_lo, mu_hi = self.daily_mu_ci(county_id, day_idx, annual_calls=annual_calls)
        hourly = []
        for h in range(24):
            w = rd.DIURNAL_WEIGHTS[h]
            lam, lam_lo, lam_hi = mu * w, mu_lo * w, mu_hi * w
            p_call = 1 - np.exp(-lam)
            p_call_lo = 1 - np.exp(-lam_lo)
            p_call_hi = 1 - np.exp(-lam_hi)

            busy_lo, busy_mid, busy_hi = availability.unavailability_band(
                lam_lo, lam, lam_hi, dm["mean_min"], dm["se_mean_min"], c_amb
            )
            crew_fail = availability.crew_assembly_failure_prob(h, is_weekend, c_roster)
            unavail_lo = 1 - (1 - busy_lo) * (1 - crew_fail)
            unavail_mid = 1 - (1 - busy_mid) * (1 - crew_fail)
            unavail_hi = 1 - (1 - busy_hi) * (1 - crew_fail)

            gap_lo = p_call_lo * unavail_lo
            gap_mid = p_call * unavail_mid
            gap_hi = p_call_hi * unavail_hi
            hourly.append({
                "hour": h,
                "expected_calls": round(lam, 4),
                "p_call": round(float(p_call), 4),
                "p_truck_busy": round(float(busy_mid), 4),
                "p_crew_assembly_failure": round(float(crew_fail), 4),
                "p_unit_busy": round(float(unavail_mid), 4),
                "gap_probability": round(float(gap_mid), 4),
                "gap_lo": round(float(gap_lo), 4),
                "gap_hi": round(float(gap_hi), 4),
            })
        return hourly, (mu, mu_lo, mu_hi)

    def horizon_hours(self, county_id, days, **ov):
        """All (date, hour-record) pairs across the forecast horizon — used to
        set a county-relative threshold for what counts as an elevated-risk
        window (absolute gap probabilities are small by construction in a
        low-volume rural county; what matters is relative elevation)."""
        out = []
        for offset in range(1, days + 1):
            day_idx = self.train_days + offset - 1
            date = self.dates.iloc[day_idx]
            hourly, _ = self.hourly_gap(county_id, day_idx, **ov)
            for h in hourly:
                out.append((date, h))
        return out

    def annualized_uncovered_calls(self, county_id, sample_every=7, **ov):
        """Expected number of 911 calls per year that arrive during an
        uncovered hour, under a given staffing/roster assumption. This is
        NOT a new assumption -- gap_probability already IS P(call arrives
        AND uncovered) for that hour, so summing it across a year's hours is
        a direct expectation, not an extrapolation. Sampled weekly (not
        every day) across the 3-year REAL weather history for speed, which
        still spans full seasonal variation (heat, storm, all months) rather
        than just the current forecast window."""
        sample_days = list(range(0, self.train_days, sample_every))
        daily_sums = []
        for day_idx in sample_days:
            hourly, _ = self.hourly_gap(county_id, day_idx, **ov)
            daily_sums.append(sum(h["gap_probability"] for h in hourly))
        mean_daily = float(np.mean(daily_sums))
        return round(mean_daily * 365, 1)

    def daily_risk(self, county_id, day_idx, **ov):
        """Daily headline risk = the day's PEAK hourly gap probability (with
        that hour's own CI), not "P(at least one gap-hour all day)". The
        latter requires treating each hour as an independent trial, which
        overstates risk here -- adjacent hours share the same day's call rate
        and overlapping call durations, so they are strongly correlated, not
        independent. Peak-hour risk is the operationally meaningful number
        anyway: it's the worst moment this county's coverage gets that day."""
        hourly, mu_ci = self.hourly_gap(county_id, day_idx, **ov)
        peak = max(hourly, key=lambda h: h["gap_probability"])
        return {
            "risk": peak["gap_probability"],
            "risk_lo": peak["gap_lo"],
            "risk_hi": peak["gap_hi"],
            "peak_hour": peak["hour"],
            "expected_calls": round(mu_ci[0], 2),
            "expected_calls_lo": round(mu_ci[1], 2),
            "expected_calls_hi": round(mu_ci[2], 2),
        }
