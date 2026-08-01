# Muster — crew-assembly reliability for volunteer fire & EMS

**The question no existing tool answers: when the tone goes out, does a crew
actually form?**

Urban EMS software optimizes where to position ambulances. A county with one
ambulance has no placement decision to make. Its failure mode is different and
almost entirely unmeasured — the page goes out and nobody is available to
answer it, because 70–74% of rural responders hold outside jobs and 55% of
agencies have six or fewer people providing 80% of their staffing.

This measures that, from an agency's own dispatch export, using the interval
between dispatch and the unit going en route. It scores against **NFPA 1720**,
the national deployment standard that exists precisely because volunteers
cannot guarantee availability the way on-duty career staff can.

Applies to **volunteer fire departments (24,208 in the US — 82% of all
departments)** as much as rural EMS; the data and the failure are identical,
and in most rural counties they are the same organization.

## Run

```
pip install -r requirements.txt
python -m uvicorn app:main --reload
```

Then open http://localhost:8000.

**Startup takes ~15 seconds** (up to ~90s on the very first run, while Python
compiles bytecode and imports statsmodels/scipy). The server prints progress —
if it looks stalled, it is importing scientific libraries, not hung.

To refresh the real weather data (e.g. for a new demo day), re-run the fetch
in `data/DATA_SOURCES.md` — the app itself does not hit the network at
startup; `data/weather_history.csv` and `data/weather_forecast.csv` are
pre-fetched flat files.

## What it does

Reuses the partial-pooling Poisson GLM from `pooling_experiment.py` /
`temporal_pooling.py` (ported into `model/temporal.py`, statistics unchanged)
to forecast daily/hourly call rates. Each of the 19 counties has its own
REAL weather-driven design matrix (real dates, real heat/storm days at that
county's centroid) rather than one shared calendar — so unlike the original
experiment, `fit_shared`/`fit_all` take a dict of per-county design matrices.

The model is pooled only within this product's actual target cohort
(population < 60,000 — 16 of the 19 counties). Nevada, Yolo, and Placer
counties are ~300x larger in call volume than Alpine; pooling across that much
scale imbalance isn't a meaningful "shared coefficient" exercise, so those
three are fit standalone and kept only as an out-of-cohort volume contrast
(see `model/fit.py`, `POOLING_POP_CEILING`).

Coverage gap probability = P(call in window) × P(all ambulances already
busy), where the busy probability comes from an Erlang-C queueing
approximation fit to modeled transport-duration records (scaled from each
county's real land area — `model/availability.py`). The daily headline number
is the day's PEAK hourly gap probability, not "P(a gap occurs at some point
that day)" — treating 24 hours as independent trials would overstate risk,
since adjacent hours share the same day's call rate.

Three decisions, surfaced as action cards per county:
1. **Staff this shift** — the worst predicted window in the forecast horizon
2. **Pre-arrange mutual aid** — a second, distinct high-risk window
3. **Visit these residents this month** — top chronic-risk block groups
   (aggregate counts and risk tiers only, never individual records — fully
   synthetic, no real sub-county microdata)

The Methods screen shows a Data Provenance panel (what's real vs modeled),
held-out performance (partial pooling vs county-only vs complete pooling,
scored on real weather history), per-coefficient shrinkage weights for the
selected county, and the pre-registered sensitivity sweep (`data/sensitivity.csv`,
simulated, method-validation only) showing where partial pooling actually helps.

## The audit: the piece you actually sell first

`model/audit.py` + `api/report.py` implement the commercial wedge: instead of
asking an agency to trust a forecast about their county, ask for their own
dispatch history and **measure what already happened**.

- **Input**: one CSV from any CAD/ePCR system. Column names are fuzzy-matched
  against aliases seen in ESO / ImageTrend / Zoll / county CAD exports; only a
  dispatch timestamp is strictly required. `GET /api/audit/template.csv`
  returns the blank file to send an agency.
- **Measure**: the dispatch -> en-route interval (crew turnout). Incidents are
  classified as *no crew formed* (mutual aid requested, no-unit disposition, or
  no en-route time) or *severe delay* (>10 min), with thresholds set against
  NFPA 1720 volunteer expectations rather than career-department norms.
- **Output**: `GET /api/audit/{id}/report` renders a printable **Coverage
  Reliability Report** — the document an agency forwards to a county board or
  attaches to a SIREN grant application (that program funds rural EMS
  recruitment, retention, and technology at up to $200k/award and requires
  exactly this kind of documented need).
- **Privacy by construction**: columns whose names match patient-data patterns
  are dropped at parse time, before any analysis. Only timestamps, unit ids,
  and dispositions are read; every output is an aggregate. Uploads are held in
  memory only and never written to disk. The report states all of this in its
  own Data Handling section.

### Coverage optimizer (`model/optimizer.py`)

Answers the question a chief cannot answer in their head: **given N staffed
hours a week you can afford, which hours?** Failures are spread across 168
hour-of-week cells; the optimizer greedily selects non-overlapping contiguous
blocks (4h minimum, because nobody staffs an hour here and an hour there) to
maximise failures removed, and returns a diminishing-returns curve with the
"knee" marked — usually the most persuasive number for a board, since it shows
where each additional staffed hour stops buying much.

A staffed crew is credited with eliminating crew-assembly failures inside its
window but **not** truck-busy failures, since a second simultaneous call still
collides with a single unit. The split is stated in the output.

### Funding packet (`api/packet.py`)

The same analysis rendered as the sections a grant reviewer asks for:
documented need, the specific ask (straight from the optimizer), projected
outcome, cost of inaction, methodology. It deliberately does **not** generate
narrative for organisational capacity, project management, or sustainability —
auto-written filler in those sections is obvious to reviewers and would hurt an
application. Those are listed as "sections to complete".

If the agency's failure rate is under 1%, the packet refuses to build a case:
it prints a **NOT A FUNDING CASE** banner instead. Generating a grant request
for an agency with no problem would discredit every other packet the tool
produces.

Why this ships before the forecast: it carries no validation burden (it
describes the past, not the future), it is immediately credible because it is
the agency's own data, it is the evidence a grant application needs, and every
audit produces the labeled training data the forecast currently lacks.

`POST /api/audit/reference` runs the whole flow on **real data**: 40,000 actual
2024 San Francisco ambulance dispatches (DataSF `nuek-vuh3`, bundled at
`data/sf_ems_2024_real.csv`, no network access at runtime). It doubles as a
control — SF runs career crews from staffed stations, so a correct audit should
find almost no crew-assembly failure, and it finds 0.13% with a 0.1-minute
median turnout. The report detects this and switches to a "no significant
problem found" framing rather than alarming language, because a tool that
manufactures findings on a well-run agency is worthless.

Running against real data immediately caught a real gap: SF's column names
(`dispatch_dttm`, `response_dttm`) were not in the alias list, which no amount
of synthetic testing would have surfaced.

## Fire departments (`model/audit.py`)

The same file, the same code path. Fire CAD exports carry the same two
timestamps; NFIRS incident types are parsed to auto-detect whether an agency is
fire, EMS, or mixed, which selects the applicable NFPA 1720 turnout target
(90s fire / 60s EMS, 90% of the time).

Validated on two real San Francisco datasets, both 2024, run unmodified:

| | Runs | Median turnout | NFPA 1720 (60s) |
|---|---|---|---|
| Ambulances | 40,000 | 0.1 min | 92.9% — passes |
| Fire engines | 25,000 | 1.4 min | **30.3% — fails** |

Same city, same year, same stations. A well-funded career department misses the
national turnout standard on ~70% of its runs and nothing currently surfaces
that to anyone. That figure also feeds ISO Public Protection Classification
reviews, where half the score is department quality including staffing.

## Cross-agency benchmark (`model/benchmark.py`)

Crew-assembly reliability does not exist as a published dataset anywhere. Every
audit contributes **one de-identified row** — service type, volume band, failure
rate, median assembly, workday penalty. No agency name, no location, no
incident, no person.

Cohorts are matched on service type and call volume, and a percentile is
withheld until at least 8 comparable agencies exist, because a percentile
computed from three agencies is noise dressed as insight. `GET
/api/audit/benchmark/cohort` reports the current size honestly — it starts at
zero.

## Showing this to a real agency

The dashboard has six tabs, ordered for a live walkthrough:

1. **Coverage** — pick a county, see the 7-day forecast, the worst predicted
   windows, and the three recommended actions. The **"Use your real numbers"**
   panel at the top lets an agency substitute their actual ambulance count,
   roster size, annual call volume, and average out-of-service minutes for
   this prototype's estimates; everything recomputes against their real
   operation. Nothing is persisted or transmitted.
2. **Audit** — upload a real dispatch export (or run the demo) and get the
   Coverage Reliability Report. This is the wedge product; see above.
3. **Impact** — quantified savings across four channels, each tagged by
   evidence strength, plus national context. No lives-saved/VSL figure,
   deliberately.
4. **Use cases** — all 19 counties ranked by modeled annual exposure and how
   much a standing mutual-aid arrangement would cover. Click any row to open
   that county. This is the "who is this for" slide: 1–2 ambulance counties
   with thin rosters dominate the top; large staffed departments sit at zero.
5. **Methods** — data provenance (real vs. modeled), calibration against the
   four counties with publicly reported call volumes, held-out model
   performance vs. baselines, mutual-aid partner table, sensitivity sweep.
6. **FAQ** — every substantive critique this project has been tested against,
   answered directly, including the ones where the critique is correct.

## Structure

```
app.py                  FastAPI entry point (`main`)
model/
  real_data.py          loads real_counties.csv + weather CSVs, builds per-county calendars, models calls
  temporal.py            partial-pooling GLM (ported from temporal_pooling.py), per-county design matrices
  availability.py         transport-duration survival fit + Erlang-C
  fit.py                  orchestration: cohort split, fit once at startup, serve from memory
  audit.py                dispatch-export parsing, crew-assembly classification, NFPA 1720
  optimizer.py            which contiguous staffed blocks remove the most failures
  benchmark.py            de-identified cross-agency cohort + percentiles
  impact.py               cost channels, each tied to a published figure
api/
  routes.py             forecast / gaps / actions / validation / impact / usecases
  audit_routes.py       upload, demo, findings, report
  report.py             the printable Coverage Reliability Report (the deliverable)
model/audit.py          CSV ingestion, PHI stripping, crew-assembly classification
model/impact.py         cost quantification from published figures
static/                   vanilla JS/CSS frontend, no build step
data/
  real_counties.csv       19 real CA counties: population, land area, coords, EMS rate, ambulance basis
  weather_history.csv      3 years of real daily weather per county (Open-Meteo)
  weather_forecast.csv     16-day real weather forecast per county (Open-Meteo)
  DATA_SOURCES.md          citations for everything real, and what's still modeled
  sensitivity.csv, FINDINGS.md   original pooling-method experiments, carried over for the Methods page
```

## Honest limitations of this prototype

- Daily/hourly call counts are a modeled Poisson process (real population x
  real per-capita rate x real weather-driven temporal effects x an assumed
  county heterogeneity term) — not observed counts. No public source exists
  at that granularity for any CA county.
- The real EMS response rate (CY2020 CEMSIS, per LEMSA) includes all response
  types (911, transfers, mutual aid), not only emergency ambulance dispatch —
  broader than a narrower "911 calls" definition would produce.
- Ambulance fleet size is verified for Alpine and Plumas counties only; the
  other 17 use a documented population/volume heuristic, which plausibly
  undercounts tourist-destination counties (e.g. Mariposa/Yosemite,
  Mono/Mammoth) where seasonal visitor demand exceeds resident population.
- The Erlang-C unit-availability model is a standard queueing approximation,
  not a discrete-event simulation of actual dispatch history.
- Confidence intervals use a normal approximation on the log scale from each
  county's own GLM standard errors, shrunk by the empirical-Bayes weight —
  reasonable for a prototype, not a substitute for a full Bayesian posterior.
