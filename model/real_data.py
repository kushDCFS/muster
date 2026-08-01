"""
Real-data loader for the rural EMS coverage prototype.

What is REAL here:
  - county names, 2024 population, land area, and centroid coordinates
    (US Census Bureau: Population Estimates Program + county Gazetteer file)
  - the temporal calendar: actual dates, actual day-of-week, actual daily
    max temperature / precipitation / snowfall at each county's centroid,
    for the last 3 years AND the next 16 days
    (Open-Meteo Historical Weather API + Forecast API, no key required)
  - each county's EMS response rate is the real CY2020 CEMSIS "responses per
    1,000 population" figure for its LEMSA region, or the real CY2020
    statewide average where the specific LEMSA isn't confirmed
    (California EMSA, SYS 100-09 Annual EMS Data Report, CY2020)
  - the "closest-analog" ambulance counts for Alpine and Plumas counties
    (public agency pages, see data/real_counties.csv `ambulance_basis` column)

What is still MODELED (no public source exists at this granularity):
  - daily/hourly CALL COUNTS themselves: no county publishes a public,
    per-day EMS dispatch log. We generate them as a Poisson process whose
    rate = (real population x real per-capita response rate) x a
    temporal-effect curve fit to that county's REAL weather/weekend calendar,
    plus an assumed county-level heterogeneity term (omega, per FINDINGS.md
    — the real value of omega for rural EMS temporal response has never been
    measured; this is a working assumption, not an observed quantity).
  - individual call transport DURATIONS (no public per-call record exists) —
    modeled as lognormal, with the median scaled by each county's REAL land
    area (bigger, sparser counties get longer modeled transport times).
  - most counties' exact ambulance FLEET SIZE — verified for Alpine and
    Plumas only; everywhere else it's `round(annual_calls_est / 2500)`,
    a documented heuristic, not a confirmed count. See `ambulance_basis`.
  - the prevention block-group risk data — fully synthetic by design; no
    real sub-county demographic or clinical microdata is used anywhere.

See data/real_counties.csv, data/weather_history.csv, data/weather_forecast.csv
for the underlying real inputs and data/DATA_SOURCES.md for citations.
"""

import numpy as np
import pandas as pd

RNG = np.random.default_rng(20260730)

DATA_DIR = "data"

BETA_SHARED = {
    "sin1": 0.10, "cos1": -0.18,   # annual seasonality
    "sin2": 0.05, "cos2": 0.04,    # semiannual
    "weekend": 0.14,               # weekend elevation
    "heat": 0.33,                  # extreme heat day (real, tmax >= 95F)
    "storm": 0.46,                 # winter storm day (real, snow/heavy precip)
}
TERMS = list(BETA_SHARED.keys())

TERM_LABELS = {
    "sin1": "annual seasonal phase",
    "cos1": "annual seasonal phase",
    "sin2": "semiannual seasonal phase",
    "cos2": "semiannual seasonal phase",
    "weekend": "weekend elevation",
    "heat": "extreme heat day (real, ≥95°F)",
    "storm": "winter storm day (real snow/heavy rain)",
}
OMEGA = 0.16   # assumed sd of true county-level deviation -- NOT an observed quantity, see FINDINGS.md

HEAT_THRESHOLD_F = 95.0
STORM_SNOW_IN = 0.5
STORM_PRECIP_IN = 1.0

_DIURNAL_RAW = np.array([
    0.010, 0.008, 0.006, 0.005, 0.006, 0.010,   # 00-05
    0.020, 0.035, 0.050, 0.058, 0.062, 0.065,   # 06-11
    0.066, 0.064, 0.062, 0.060, 0.058, 0.055,   # 12-17
    0.050, 0.045, 0.038, 0.030, 0.020, 0.014,   # 18-23
])
DIURNAL_WEIGHTS = _DIURNAL_RAW / _DIURNAL_RAW.sum()


def _base_calendar(dates):
    t = np.arange(len(dates))
    doy = dates.dt.dayofyear.values
    ang = 2 * np.pi * doy / 365.25
    dow = dates.dt.dayofweek.values
    return pd.DataFrame({
        "day": t,
        "date": dates,
        "sin1": np.sin(ang), "cos1": np.cos(ang),
        "sin2": np.sin(2 * ang), "cos2": np.cos(2 * ang),
        "weekend": ((dow == 5) | (dow == 6)).astype(float),
    })


def _weather_flags(weather_df):
    heat = (weather_df["tmax_f"] >= HEAT_THRESHOLD_F).astype(float)
    storm = ((weather_df["snow_in"] >= STORM_SNOW_IN) | (weather_df["precip_in"] >= STORM_PRECIP_IN)).astype(float)
    return heat.values, storm.values


def load_counties():
    df = pd.read_csv(f"{DATA_DIR}/real_counties.csv")
    df = df.reset_index(drop=True)
    df.insert(0, "county_id", df.index)
    df["daily_base_calls"] = df["annual_calls_est"] / 365.0
    # modeled transport time from REAL land area: bigger/sparser -> longer.
    # Calibration note: an earlier version (15 + 9*log1p) pushed several
    # single-ambulance counties to 25-50% average truck-busy utilization --
    # far above the real, documented rural EMS unit-hour utilization rate of
    # 5-15% (vs. 30-50% urban targets). Real rural agencies deliberately
    # over-provision unit-hours relative to bare queueing need (~3x more
    # unit-hours per transport than urban, per RUPRI), which this model
    # doesn't capture directly -- so the duration input is dampened instead,
    # to keep modeled utilization in the range the literature actually
    # reports. Still an estimate, not an observed duration.
    df["transport_median_min"] = (12 + 4 * np.log1p(df["land_sqmi"] / 100)).round(1)
    # estimated volunteer roster size, NOT observed: ~8 per unit, informed by
    # RUPRI/NRHA finding that most rural agencies run on 6 or fewer people
    # providing 80%+ of staffing (per unit, roughly). For the 3 out-of-cohort
    # counties (Nevada/Yolo/Placer), this heuristic yields a large effective
    # roster, which correctly reflects that they run staffed/paid departments,
    # not thin volunteer rosters -- so crew-assembly failure is negligible
    # there, matching reality, not because we verified their actual staffing.
    df["roster_size_est"] = (df["num_ambulances"] * 8).clip(lower=6)
    return df


def _haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def compute_mutual_aid_partners(counties):
    """For each county, the REAL-geography closest other county in this
    dataset (by real centroid coordinates) -- physical proximity is what
    actually determines whether mutual aid is feasible (you can't quickly
    send a truck 100+ miles), so distance is the primary criterion. Also
    flags whether they share a CONFIRMED real LEMSA (the two counties
    already coordinate under one regional EMS agency) as bonus context, not
    as an override -- a same-LEMSA county on the far side of the region is
    not a better mutual-aid partner than a genuinely adjacent one.
    Returns {county_id: {partner_id, partner_name, distance_mi, same_lemsa}}."""
    out = {}
    for _, row in counties.iterrows():
        best = None
        for _, other in counties.iterrows():
            if other.county_id == row.county_id:
                continue
            d = _haversine_miles(row.lat, row.lon, other.lat, other.lon)
            if best is None or d < best[0]:
                same_lemsa = (
                    row.rate_basis == other.rate_basis
                    and str(row.rate_basis).startswith("LEMSA")
                )
                best = (d, other, same_lemsa)
        d, other, same_lemsa = best
        out[int(row.county_id)] = {
            "partner_id": int(other.county_id),
            "partner_name": other["name"],
            "distance_mi": round(float(d), 1),
            "same_lemsa": bool(same_lemsa),
        }
    return out


def build_calendars(counties):
    """Returns (train_days, forecast_days, dates: pd.Series, calendars: dict[county_id -> DataFrame with TERMS+date]).
    The non-weather columns (sin1/cos1/sin2/cos2/weekend) are identical across
    counties because they were fetched over the same real date range; heat and
    storm come from that county's own real weather series."""
    hist = pd.read_csv(f"{DATA_DIR}/weather_history.csv", parse_dates=["date"])
    fcst = pd.read_csv(f"{DATA_DIR}/weather_forecast.csv", parse_dates=["date"])

    hist_dates = pd.DatetimeIndex(sorted(hist.date.unique()))
    fcst_dates = pd.DatetimeIndex(sorted(fcst.date.unique()))
    all_dates = hist_dates.append(fcst_dates)
    train_days = len(hist_dates)
    forecast_days = len(fcst_dates)

    base_cal = _base_calendar(pd.Series(all_dates))
    dates = base_cal["date"]

    calendars = {}
    for _, co in counties.iterrows():
        name = co["name"]
        h = hist[hist.name == name].sort_values("date").reset_index(drop=True)
        f = fcst[fcst.name == name].sort_values("date").reset_index(drop=True)
        heat_h, storm_h = _weather_flags(h)
        heat_f, storm_f = _weather_flags(f)
        cal = base_cal.copy()
        cal["heat"] = np.concatenate([heat_h, heat_f])
        cal["storm"] = np.concatenate([storm_h, storm_f])
        calendars[int(co.county_id)] = cal

    return train_days, forecast_days, dates, calendars


def simulate_calls(counties, calendars, train_days):
    """Daily call counts over the REAL historical calendar. The counts
    themselves are a modeled Poisson process (see module docstring) --
    real population, real per-capita rate, real weather-driven covariates,
    but no observed per-day dispatch log exists to draw from."""
    beta_shared = np.array([BETA_SHARED[k] for k in TERMS])
    n_c = len(counties)
    counts = {}
    beta_county = {}
    for _, co in counties.iterrows():
        cid = int(co.county_id)
        Z = calendars[cid][TERMS].values[:train_days]
        beta_c = beta_shared + RNG.normal(0, OMEGA, len(TERMS))
        lam = co.daily_base_calls * np.exp(Z @ beta_c)
        counts[cid] = RNG.poisson(lam)
        beta_county[cid] = beta_c
    return counts, beta_county


def simulate_transport_durations(counties, calendars, counts, train_days):
    rows = []
    hours = np.arange(24)
    for _, co in counties.iterrows():
        cid = int(co.county_id)
        dates = calendars[cid]["date"].values[:train_days]
        day_counts = counts[cid]
        med = co.transport_median_min
        sigma = 0.38
        mu_log = np.log(med)
        for day_idx in np.nonzero(day_counts > 0)[0]:
            n_calls = int(day_counts[day_idx])
            call_hours = RNG.choice(hours, size=n_calls, p=DIURNAL_WEIGHTS)
            durations = RNG.lognormal(mu_log, sigma, size=n_calls)
            date = dates[day_idx]
            for h, d in zip(call_hours, durations):
                rows.append((cid, date, int(h), float(d)))
    return pd.DataFrame(rows, columns=["county_id", "date", "hour", "duration_min"])


def simulate_prevention_blocks(counties):
    """Fully synthetic block-group-level risk data -- aggregate counts and
    risk tiers only, no individual records, no real sub-county microdata."""
    rows = []
    for _, co in counties.iterrows():
        cid = int(co.county_id)
        n_blocks = RNG.integers(4, 9)
        block_pop = RNG.dirichlet(np.ones(n_blocks) * 3) * co.population
        pct_65plus = RNG.normal(0.19, 0.06, n_blocks).clip(0.05, 0.45)
        chronic_rate = RNG.beta(2.0, 6.0, n_blocks)
        risk_score = 0.55 * pct_65plus + 0.45 * chronic_rate + RNG.normal(0, 0.03, n_blocks)
        high_acuity_count = np.round(block_pop * chronic_rate * (0.4 + 0.6 * pct_65plus)).astype(int)
        for i in range(n_blocks):
            rows.append({
                "county_id": cid,
                "block_group": f"BG-{i+1}",
                "population": int(round(block_pop[i])),
                "pct_65plus": round(float(pct_65plus[i]), 3),
                "high_acuity_count": int(high_acuity_count[i]),
                "risk_score": round(float(risk_score[i]), 3),
            })
    return pd.DataFrame(rows)


def generate_all():
    counties = load_counties()
    train_days, forecast_days, dates, calendars = build_calendars(counties)
    counts, beta_true = simulate_calls(counties, calendars, train_days)
    durations = simulate_transport_durations(counties, calendars, counts, train_days)
    prevention = simulate_prevention_blocks(counties)
    return {
        "counties": counties,
        "calendars": calendars,
        "dates": dates,
        "train_days": train_days,
        "forecast_days": forecast_days,
        "counts": counts,
        "beta_true": beta_true,
        "durations": durations,
        "prevention": prevention,
    }
