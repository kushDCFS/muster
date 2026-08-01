# Data sources

## Real

- **County names, 2024 population estimates**: US Census Bureau, Population
  Estimates Program, `co-est2024-alldata.csv`
  (https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/counties/totals/)
- **Land area, centroid coordinates**: US Census Bureau, 2024 Gazetteer Files,
  California counties
  (https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/)
- **Daily weather, last 3 years** (max temperature, precipitation, snowfall) at
  each county's centroid: Open-Meteo Historical Weather API
  (https://archive-api.open-meteo.com/v1/archive), no key required
- **Weather forecast, next 16 days**: Open-Meteo Forecast API
  (https://api.open-meteo.com/v1/forecast), no key required
- **EMS response rate per 1,000 population (CY2020)**, by LEMSA region:
  California EMS Authority, *SYS 100-09 Annual EMS Data Report, Calendar Year
  2020* (Chart 2 / Table 3), https://emsa.ca.gov/. Applied to a county when its
  LEMSA membership was confirmed from public agency pages; the statewide
  CY2020 average is used otherwise (see `rate_basis` column).
- **Ambulance provider structure for Alpine and Plumas counties**: Mountain
  Valley EMS Agency (mvemsa.org) and Plumas District Hospital / Sierra
  Regional EMS public pages (see `ambulance_basis` column in
  `real_counties.csv`).

## Modeled (no public source exists at this granularity)

- **Daily/hourly call counts**: no county publishes a public per-day EMS
  dispatch log. Generated as a Poisson process: real population x real
  per-capita response rate x a temporal-effect curve fit to the county's real
  weather/weekend calendar, plus an assumed county-level heterogeneity term
  (omega = 0.16, per `FINDINGS.md` — the true value has never been measured
  for rural EMS temporal response).
- **Per-call transport durations**: lognormal, median scaled by each county's
  real land area. No public per-call duration dataset exists.
- **Ambulance fleet size** for all counties except Alpine and Plumas:
  `round(annual_calls_est / 2500)`, a documented heuristic — not a verified
  count. See `ambulance_basis`.
- **Volunteer roster size** (`roster_size_est`): `num_ambulances x 8`, clipped
  at a floor of 6 — a heuristic, not a real roster count.
- **Crew-assembly failure probability**: the coverage-gap model combines
  P(truck busy) with P(crew fails to assemble), since the rural EMS
  literature documents the latter as the actual binding constraint in most
  rural systems (rural unit-hour utilization runs ~5-15%, so simultaneous-call
  collisions are rare; see `model/availability.py` for the full rationale).
  The per-volunteer availability-by-time-of-day numbers feeding that term
  (`Q_WEEKDAY_WORKDAY = 0.16`, `Q_OFF_HOURS = 0.38`, `Q_WEEKEND = 0.42`) are a
  labeled assumption informed by, but not fit to, the following real findings:
    - 70-74% of rural EMS providers are not full-time EMS and carry outside
      jobs (NRHA rural EMS workforce research)
    - 63% of Wisconsin's volunteer-staffed EMS agencies do not provide
      continuous 24/7/365 coverage (Wisconsin Public Radio / rural EMS
      workforce reporting)
    - 55% of rural EMS agencies have 6 or fewer people providing 80%+ of
      their staffing (RUPRI, *Characteristics and Challenges of Rural
      Ambulance Agencies*, January 2021)
  No public per-county roster-response dataset exists to calibrate this
  directly — closing that gap (i.e. getting a real answer to "what fraction
  of tone-outs fail to form a crew, and when") is the single highest-value
  next step for this part of the model, the same way real NEMSIS data was
  flagged as the highest-value next step for the pooling method in
  `FINDINGS.md`.
- **Prevention block-group risk data**: fully synthetic by design. No real
  sub-county demographic or clinical microdata is used anywhere in this
  prototype.

## Mutual aid partner selection

For each county, the "Pre-arrange mutual aid" action names a specific
neighboring county rather than a generic placeholder. The partner is chosen
by **real geographic distance** between county centroids (haversine distance
on the real lat/lon from the Census Gazetteer) — physical proximity is the
dominant factor in whether mutual aid is actually feasible, so the closest
other county in this 19-county dataset is picked, not the one that merely
shares a LEMSA. Whether the pair is also a **confirmed real LEMSA match**
(both counties' `rate_basis` points to the same real regional EMS agency) is
reported separately as bonus context — a documented existing coordination
relationship — not used to override distance (an early version did this and
produced Siskiyou → Glenn County, 137 miles apart, because they share a very
large LEMSA; distance-first gives Siskiyou → Trinity County, 72 miles, which
is the real adjacent county). All 19 pairings match real California county
adjacency. See `model/real_data.py:compute_mutual_aid_partners` and the
"Mutual aid partner, by county" table on the Methods tab.

## Real-world calibration check

Four counties in this dataset have a real, publicly reported annual call
volume that the modeled `annual_calls_est` can be checked against (see the
"Model calibration" panel on the Methods tab, and `real_world_calibration` in
the `/api/validation` response):

| County | Real reported calls/yr | Source |
|---|---|---|
| Tuolumne | ~8,000 (ongoing) | Tuolumne County Ambulance Service, official page (tuolumnecounty.ca.gov/299/Ambulance) |
| Lassen | 3,354 (2025) | SEMSA annual report to the Lassen County EMCC, via Sierra Daily News |
| Del Norte | 4,546 (2024) | EndPoint EMS Consulting assessment, via Redwood Voice |
| Mono | 1,710 (FY2020) | Mono County EMS budget report (OpenGov) |

The model runs consistently low against all four (13-41%), which is reported
as-is rather than corrected for -- with only 4 real data points across 19
counties, adjusting the rate to match would be overfitting to a tiny sample.
The direction and spread are themselves informative: a single
statewide/regional per-capita rate can't capture real county-to-county
variation, which is exactly the problem partial pooling with real per-county
history is meant to solve.

## Quantifying impact: what's computed vs. what's deliberately not claimed

- **Annualized uncovered calls** (`annual_uncovered_calls_now` /
  `_with_mutual_aid` in `/api/counties/{id}/actions`, `Fitted.annualized_uncovered_calls`):
  each hour's `gap_probability` already equals P(a call arrives AND the
  county is uncovered that hour), so summing it across a representative
  sample of real weather days (every 7th day across the 3-year history, to
  keep compute cheap while still spanning all seasons) and scaling to 365
  days is a direct expectation, not a new assumption layered on top.
- **Prevention savings range** (`estimated_annual_savings_low/high` on the
  prevention action): `target_count` (residents flagged) x a $1,000-1,900/
  person/year range drawn from real published community paramedicine
  programs (a rural Nova Scotia program's per-person annual cost fell from
  $2,380 to $1,375; a Washington MIH-CP program realized ~$1,545-1,900/
  member/year in net savings). This is explicitly a range based on other
  programs' documented outcomes, not a measurement of this county's actual
  residents.
- **Deliberately not computed**: a "lives saved" or dollar figure derived by
  applying a Value of Statistical Life calculation to a coverage-gap
  reduction. The response-time-to-survival literature (115-study systematic
  review, Pons et al.) shows insufficient evidence for that causal link at
  the margins this product operates in — monetizing it anyway would be
  exactly the "rigged win" this project's FINDINGS.md was written to avoid.

### Audit reference dataset (REAL)

`data/sf_ems_2024_real.csv` — **40,000 actual ambulance dispatches**, San
Francisco Fire Department, calendar year 2024, filtered to `unit_type = MEDIC`.
Source: [DataSF, Fire Department and EMS Dispatched Calls for Service, dataset
`nuek-vuh3`](https://data.sfgov.org/Public-Safety/Fire-Department-and-Emergency-Medical-Services-Dis/nuek-vuh3).
Columns kept: incident number, unit id, dispatch/en-route/on-scene/available
timestamps, final disposition. No patient-level field is present in the source
extract or the bundled file.

Running the audit on it yields: **median turnout 0.1 min**, p90 0.8 min,
**0.13% failure rate**, weekday-daytime penalty ratio 1.47 (i.e. essentially
flat).

**Why this dataset, and its limitation.** It is a deliberate *control*. SF runs
career crews from continuously staffed stations, so a correct audit should find
almost no crew-assembly failure — and it does. That validates the method
(it reports what is in the data instead of manufacturing a finding) and gives
the baseline a volunteer agency is compared against. SF is **not** the rural
volunteer service this product targets. Incident-level dispatch/en-route
timestamps are published by metropolitan agencies and almost never by rural
volunteer ones — which is exactly why the product asks an agency for its own
export rather than relying on public data.

Datasets checked and rejected for this purpose: **Allegheny County 911
Dispatches** (2.4M records covering 130 municipalities including rural
townships, but aggregated to quarter/year with no incident timestamps, so
turnout cannot be computed); **NYC EMS Incident Dispatch Data** (has the
timestamps, but is equally urban and career-staffed).

## Impact-model sources (`model/impact.py`)

Every constant is real and published; applying them to a county here is an
estimate built on modeled coverage numbers, not a measured local result.

| Figure | Value | Source |
|---|---|---|
| Cost per EMS departure | $6,872 median ($6,780 EMT / $9,113 paramedic) | EMS1, "Turnover: the cost of replacing an EMT" + agency survey |
| EMS turnover rate | 20–30%/yr; 32% leave in year one | Same |
| EMS burnout | 73% report burnout; 37% plan to leave within 5 yrs | JEMS first-responder mental health reporting |
| Retention gain from schedule predictability | **5–15%, assumed** | NVFC volunteer retention research — documented *directionally only*, never quantified for EMS. Softest number in the model. |
| Prevention savings/person/yr | $1,000–1,900 | Nova Scotia rural CP ($2,380→$1,375/person/yr); Washington MIH-CP (63% drop in 911 use); avoided ED visit ~$1,900 |
| Realistic CP caseload | ~150 patients/paramedic | Program-scale reality check — see below |
| Mutual aid fee | $350/request | South County EMS → Hatfield published rate |
| Under-reimbursement | $1,526/transport | CMS Ground Ambulance Data Collection System, Dec 2024 |
| Ambulance deserts | 4.5M people (2.3M rural); 4 of 5 rural counties | Univ. of Southern Maine / Federal Office of Rural Health Policy, 41 states |
| Rural hospital closures | 70+ since 2013; transport times up to +76% | UNC Gillings / Rural Health Research Gateway |

**Caseload realism.** An earlier version multiplied published per-person
prevention savings by *every* resident the risk model flags. For Mariposa
County that was ~1,890 people — roughly 11% of the county's entire
population — producing a $1.9–3.6M/yr headline. No real community
paramedicine program enrolls a tenth of a county, so savings are now computed
on a realistic enrolled caseload (~150/paramedic), giving ~$150–287K/yr. The
flagged count is still reported separately as a measure of unmet need.

**No lives-saved / VSL figure is computed anywhere**, deliberately — the
response-time-to-survival literature does not support that link at these
margins (see FAQ).

### Duration-model recalibration (Erlang-C truck-busy term)

An earlier version of `transport_median_min` (`15 + 9*log1p(land_sqmi/100)`)
combined with real call rates pushed several single-ambulance counties to
25-50% average truck-busy utilization in the Erlang-C model — far above the
documented real rural EMS unit-hour utilization rate of 5-15% (vs. 30-50%
urban targets; rural agencies typically run ~3x more unit-hours per
transport than urban ones, per RUPRI). Retuned to `12 + 4*log1p(land_sqmi/100)`,
which brings most counties into single digits to high teens, with a couple
of real outliers (Inyo, Colusa — high call-to-truck ratio and/or large land
area) still running higher. Still an estimate, not an observed duration; the
same "our own num_ambulances heuristic may undercount this county" caveat
from `README.md` applies to any county whose modeled utilization looks high.

## Caveat on the EMS response rate

The CY2020 CEMSIS "EMS responses per 1,000 population" figure includes all
response types (911, interfacility transfer, medical transport, mutual aid),
not only emergency ambulance dispatches. It is the finest-grained real,
public per-capita EMS activity rate available for California and is used
here as the best available anchor for expected call volume — but it is
broader than "911 emergency calls" alone, which should read as somewhat
higher than a narrower definition would produce.
