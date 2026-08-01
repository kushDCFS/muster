"""
Impact quantification: translating the coverage model into the outcomes a
county administrator actually budgets against.

EVERY constant below is a real, published, cited figure -- but applying them
to a county in this prototype is an ESTIMATE built on the modeled coverage
numbers, not a measured result for that county. The distinction is preserved
in the API output (`basis` on every channel) and surfaced in the UI.

Deliberately NOT included: any lives-saved or Value-of-Statistical-Life
figure. The response-time-to-survival literature (115-study systematic
review; Pons et al.) does not support that link at the margins this product
operates in, so monetizing it would be the exact "rigged win" FINDINGS.md
was written to avoid. Every channel here is a cost or workload figure that
stands on its own.
"""

# ---- workforce / turnover -------------------------------------------------
# "Turnover: The cost of replacing an EMT" (EMS1) and the associated agency
# survey: onboarding a new EMT ~$6,780; replacing a full-time paramedic
# ~$9,113; weighted median cost per termination across agency types $6,871.51.
COST_PER_DEPARTURE = 6872           # weighted median cost per termination
TURNOVER_RATE_LOW = 0.20            # published EMS voluntary turnover range
TURNOVER_RATE_HIGH = 0.30
# Retention effect of predictable scheduling is documented directionally
# (NVFC volunteer retention research: self-scheduling and schedule control
# improve retention) but has NEVER been quantified for EMS specifically.
# This is the single softest number in the model and is labeled as such.
RETENTION_IMPROVEMENT_LOW = 0.05
RETENTION_IMPROVEMENT_HIGH = 0.15

# ---- prevention / avoided utilization ------------------------------------
# Nova Scotia rural CP program: per-person annual health cost $2,380 -> $1,375.
# Washington MIH-CP: ~63% drop in 911 use among enrolled adults.
# Broader literature: avoided ED visit ~$1,900; primary-care-treatable
# condition costs $2,032 at an ED vs $167 at a physician office.
PREVENTION_SAVINGS_LOW = 1000
PREVENTION_SAVINGS_HIGH = 1900

# ---- mutual aid -----------------------------------------------------------
# Real published mutual-aid service fee: South County EMS charges Hatfield
# $350 per ambulance request. Used only to show that PROACTIVE coverage is
# not free -- the point is that it's small relative to the exposure it covers.
MUTUAL_AID_FEE = 350

# ---- transport economics --------------------------------------------------
# CMS Ground Ambulance Data Collection System (GADCS), Dec 2024 report:
# mean reimbursement per transport $1,147; agencies under-reimbursed by an
# average of $1,526 per transport.
UNDERREIMBURSEMENT_PER_TRANSPORT = 1526


def workforce_impact(roster_size):
    """Annual turnover cost exposure for a roster this size, and the range a
    documented-but-unquantified retention improvement would represent."""
    roster = max(float(roster_size), 1.0)
    departures_low = roster * TURNOVER_RATE_LOW
    departures_high = roster * TURNOVER_RATE_HIGH
    exposure_low = departures_low * COST_PER_DEPARTURE
    exposure_high = departures_high * COST_PER_DEPARTURE
    return {
        "roster_size": round(roster, 0),
        "expected_departures_per_year_low": round(departures_low, 1),
        "expected_departures_per_year_high": round(departures_high, 1),
        "turnover_cost_exposure_low": round(exposure_low),
        "turnover_cost_exposure_high": round(exposure_high),
        "retention_savings_low": round(exposure_low * RETENTION_IMPROVEMENT_LOW),
        "retention_savings_high": round(exposure_high * RETENTION_IMPROVEMENT_HIGH),
        "basis": (
            f"Published EMS turnover is {TURNOVER_RATE_LOW:.0%}-{TURNOVER_RATE_HIGH:.0%}/yr at a "
            f"weighted median ${COST_PER_DEPARTURE:,} per departure. The retention-savings range "
            f"assumes a {RETENTION_IMPROVEMENT_LOW:.0%}-{RETENTION_IMPROVEMENT_HIGH:.0%} improvement "
            "from more predictable scheduling -- documented directionally in volunteer-retention "
            "research but NEVER quantified for EMS. Treat this channel as the softest of the four."
        ),
    }


# A community paramedic can realistically carry an active caseload on the
# order of ~150 patients. Published programs enroll tens to low thousands --
# a Washington MIH-CP program connected 1,679 adults, but across a far larger
# population than a rural county. Savings are therefore computed on a
# REALISTIC ENROLLED CASELOAD, not on everyone the risk model flags:
# multiplying published per-person savings by every at-risk resident in a
# county would imply enrolling ~10% of the entire population, which no real
# program does and which a county administrator would rightly reject.
CASELOAD_PER_PARAMEDIC = 150


def prevention_impact(residents_flagged, paramedics=1):
    flagged = max(int(residents_flagged), 0)
    capacity = int(CASELOAD_PER_PARAMEDIC * max(paramedics, 1))
    enrolled = min(flagged, capacity)
    return {
        "residents_flagged": flagged,
        "realistic_enrollment": enrolled,
        "caseload_capacity": capacity,
        "savings_low": enrolled * PREVENTION_SAVINGS_LOW,
        "savings_high": enrolled * PREVENTION_SAVINGS_HIGH,
        "basis": (
            f"Savings are computed on a REALISTIC CASELOAD of {enrolled:,} enrolled patients "
            f"(~{CASELOAD_PER_PARAMEDIC} per community paramedic), not on all {flagged:,} residents the "
            "risk model flags -- enrolling every flagged resident would mean signing up a large share of "
            "the county's entire population, which no real program does. Per-person savings come from "
            "real published programs (rural Nova Scotia: $2,380 -> $1,375/person/yr; Washington MIH-CP: "
            "63% drop in 911 use; avoided ED visit ~$1,900). This is the best-evidenced channel -- one "
            "source is a randomized controlled trial -- but the range is from OTHER counties' programs, "
            "not measured outcomes here."
        ),
    }


def coverage_impact(uncovered_now, uncovered_after, annual_calls):
    avoided = max(uncovered_now - uncovered_after, 0.0)
    activations = avoided
    return {
        "uncovered_now": round(uncovered_now, 1),
        "uncovered_with_mutual_aid": round(uncovered_after, 1),
        "calls_moved_to_covered": round(avoided, 1),
        "share_of_all_calls_pct": round(100 * avoided / annual_calls, 1) if annual_calls else 0.0,
        "mutual_aid_fee_if_all_activated": round(activations * MUTUAL_AID_FEE),
        "basis": (
            "Uncovered-call counts are a direct sum of the hourly coverage model over a full year "
            f"of real weather. The fee line applies a real published mutual-aid rate (${MUTUAL_AID_FEE}/"
            "request, South County EMS) to show that proactive backup has a real but modest cost -- "
            "it assumes every avoided gap becomes a billed activation, which is a deliberate "
            "worst-case for the cost side."
        ),
    }


def transport_economics(annual_calls):
    n = max(float(annual_calls), 0.0)
    return {
        "annual_calls": round(n),
        "underreimbursement_exposure": round(n * UNDERREIMBURSEMENT_PER_TRANSPORT),
        "basis": (
            f"CMS Ground Ambulance Data Collection System (Dec 2024): agencies are under-reimbursed "
            f"by an average of ${UNDERREIMBURSEMENT_PER_TRANSPORT:,} per transport. Shown as context "
            "for why a call avoided upstream is worth more to a rural agency than a call reimbursed -- "
            "NOT as savings this tool produces."
        ),
    }
