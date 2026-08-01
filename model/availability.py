"""
Unit-availability model: P(a 911 call goes uncovered at a given hour).

This combines TWO distinct mechanisms, deliberately kept separate because
they matter very differently for rural vs. urban EMS:

  1. Truck-busy: P(the ambulance is already out on another call), from an
     Erlang-C queueing approximation fit to observed transport durations.
     This is the classic urban model. In rural systems it is usually a
     MINOR contributor: rural unit-hour utilization typically runs 5-15%
     (vs. urban targets of 30-50%), so simultaneous-call collisions are
     rare by construction (see DATA_SOURCES.md).

  2. Crew-assembly failure: P(fewer than a minimum crew can be reached and
     is available to staff the truck for this tone-out) — independent of
     whether the truck itself is busy. This is the mechanism the rural EMS
     literature actually documents as binding: 70-74% of rural EMS
     providers are not full-time EMS and carry outside jobs (NRHA rural
     workforce research), so weekday daytime hours are a real,
     structurally lower-availability window than nights/weekends, and
     63% of Wisconsin's volunteer-staffed agencies fail to provide
     continuous 24/7/365 coverage at all. No public per-county roster
     schedule exists, so the per-volunteer availability probabilities
     below are a documented, labeled ASSUMPTION informed by that
     literature -- not calibrated to any real county's actual roster.

Final P(uncovered) = 1 - P(truck free) x P(crew assembles | truck free).
Both are approximations, not a discrete-event simulation of real dispatch
history -- treat these as order-of-magnitude estimates.
"""

import numpy as np
from scipy import stats


def fit_duration_model(durations_df, county_id):
    d = durations_df.loc[durations_df.county_id == county_id, "duration_min"]
    if len(d) < 5:
        return {"mean_min": 30.0, "se_mean_min": 10.0, "n": int(len(d))}
    shape, loc, scale = stats.lognorm.fit(d.values, floc=0)
    mean_min = float(stats.lognorm.mean(shape, loc=loc, scale=scale))
    # bootstrap SE of the mean, cheap and good enough for a CI band
    boot = np.array([
        stats.lognorm.mean(*stats.lognorm.fit(
            np.random.default_rng(i).choice(d.values, size=len(d), replace=True), floc=0
        ))
        for i in range(60)
    ])
    return {"mean_min": mean_min, "se_mean_min": float(boot.std()), "n": int(len(d))}


def _erlang_c(offered_load_a, c):
    """P(all c servers busy) for arrival rate*service-time = a, c servers."""
    if c <= 0:
        return 1.0
    if offered_load_a <= 0:
        return 0.0
    rho = offered_load_a / c
    if rho >= 0.995:
        return 0.99
    b = 1.0
    for n in range(1, c + 1):
        b = (offered_load_a * b) / (n + offered_load_a * b)
    erlang_c = (c * b) / (c - offered_load_a * (1 - b))
    return float(np.clip(erlang_c, 0.0, 0.99))


def unavailability_prob(calls_per_hour, mean_service_min, num_ambulances):
    """P(all units busy) given an hourly call rate and mean transport duration."""
    service_hours = mean_service_min / 60.0
    a = calls_per_hour * service_hours
    return _erlang_c(a, num_ambulances)


def unavailability_band(calls_per_hour_lo, calls_per_hour, calls_per_hour_hi,
                         mean_service_min, se_service_min, num_ambulances):
    """Rough CI on P(all busy): sweep call-rate and service-time uncertainty
    to their low/high combinations and take the envelope."""
    lo = unavailability_prob(calls_per_hour_lo, max(mean_service_min - se_service_min, 1.0), num_ambulances)
    mid = unavailability_prob(calls_per_hour, mean_service_min, num_ambulances)
    hi = unavailability_prob(calls_per_hour_hi, mean_service_min + se_service_min, num_ambulances)
    return min(lo, mid, hi), mid, max(lo, mid, hi)


# ---- crew-assembly failure -------------------------------------------
#
# See module docstring for the honesty caveat: these per-volunteer
# probabilities are a documented ASSUMPTION, not a fitted or observed
# quantity. They're deliberately conservative and deliberately simple --
# a single number for "workday" and one for "off-hours" -- because no real
# per-county roster-response data exists to fit anything more granular.

CREW_SIZE_NEEDED = 2      # typical minimum crew (EMT + driver) to run one unit
WORKDAY_START, WORKDAY_END = 8, 18   # weekday hours when most volunteers are
                                       # plausibly at an outside job, per the
                                       # "70-74% not full-time EMS" finding
#
# Calibration note: an earlier pass (0.16 / 0.38 / 0.42) produced an
# annualized "uncovered calls" estimate of ~30% of a county's total call
# volume -- implausibly high for real systems that haven't made national
# news for failing a third of their calls. Retuned so a typical 1-truck,
# 8-person-roster county lands in the single-digit-percent range annually:
# serious and worth building a product around, not a system in open
# collapse. Still a labeled assumption, not a fitted quantity.
Q_WEEKDAY_WORKDAY = 0.5    # illustrative per-volunteer reachable-and-available prob
Q_OFF_HOURS = 0.68         # weekday evenings/nights
Q_WEEKEND = 0.72


def volunteer_reachable_prob(hour, is_weekend):
    """Illustrative per-volunteer availability -- see module docstring."""
    if is_weekend:
        return Q_WEEKEND
    if WORKDAY_START <= hour < WORKDAY_END:
        return Q_WEEKDAY_WORKDAY
    return Q_OFF_HOURS


def crew_assembly_failure_prob(hour, is_weekend, roster_size):
    """P(fewer than CREW_SIZE_NEEDED of the volunteer roster are reachable
    and available), modeled as a binomial over independent volunteers.
    Larger rosters (or paid/staffed departments, modeled as a large
    effective roster) push this toward ~0, matching the real pattern that
    this failure mode is specific to thin volunteer rosters, not staffed
    urban/suburban systems."""
    q = volunteer_reachable_prob(hour, is_weekend)
    n = max(int(roster_size), 1)
    return float(stats.binom.cdf(CREW_SIZE_NEEDED - 1, n, q))
