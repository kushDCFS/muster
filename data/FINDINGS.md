# Rural EMS pooling: what the experiments found

Three experiments testing the claim the whole product rests on — that pooling
data across counties makes prediction viable at rural call volumes where
single-county modeling fails.

All data are **simulated** from a known generating process. This validates the
method and maps its operating range. It is not evidence about real counties.

---

## Experiment 1 — annual call volume

Predicting a county's annual call total.

| county volume | county-only MAE | partial pooling MAE | gain |
|---|---|---|---|
| 65 calls/yr | 16.33 | 16.15 | 1.1% |
| 159 | 57.52 | 57.35 | 0.3% |
| 614 | 215.28 | 214.92 | 0.2% |

**Pooling adds almost nothing.** Three years of history gives ~600 observations
of a county's rate. That is plenty. Predicting annual volume is an easy problem
and pooling cannot improve on easy.

Cold start (county with no history at all): pooled covariate model beat a
statewide-average baseline by 9.4%. Real, but modest.

## Experiment 2 — daily temporal structure

Predicting *which days* carry elevated risk — seasonality, day of week, heat,
winter storms. This is what the product actually forecasts.

| | held-out deviance | top-decile lift |
|---|---|---|
| county-only | 0.9595 | 1.660x |
| complete pooling | 0.9978 | 1.504x |
| partial pooling | 0.9605 | 1.661x |

**Partial pooling ties county-only and beats complete pooling by 3.7%.**
Still no unlock.

## Experiment 3 — sensitivity sweep

Why the tie? Shrinkage weight is `w = ω²/(ω² + se²)`, where ω is true
between-county spread and se is a county's own sampling error. Pooling helps
only when ω is small relative to se. Experiment 2 assumed ω = 0.16, large
enough that each county's own data wins.

Gain of partial pooling over county-only (%), **1 year of history**:

| calls/yr | ω=0.05 | 0.10 | 0.15 | 0.20 | 0.30 | 0.40 |
|---|---|---|---|---|---|---|
| 50 | 3.1 | 7.7 | 9.4 | **10.4** | 5.0 | -0.6 |
| 100 | 2.5 | 3.8 | 1.3 | 0.9 | 0.3 | 6.2 |
| 200 | 1.6 | 1.0 | 0.9 | 0.3 | -0.4 | -4.4 |
| 400 | 1.0 | 1.3 | 0.3 | -0.5 | -3.6 | -9.7 |
| 800 | 0.9 | 0.4 | -0.3 | -1.1 | -4.7 | -15.2 |

With **3 years of history**, gains collapse to under 1% everywhere and go
sharply negative at high ω.

---

## What this means

**1. The premise is partly wrong.** "Rural counties can't build predictive
models because call volume is too low" does not hold at 200+ calls/year with a
few years of history. Those counties have enough data to model themselves.
Pooling pays only in the genuinely data-starved corner: roughly **under 100
calls/year with about one year of usable history.**

**2. The real barrier is not statistical.** Rural agencies lack analytics staff,
software, and budget — not data. That is an economic and operational problem,
and it is a perfectly good problem to build a company around. It is just a
different pitch than "we solved small-N."

**3. Partial pooling is still the right method,** for a narrower reason: it is
never much worse than the better of the two extremes, and it beats naive
lumping by 4–37% depending on heterogeneity. A non-expert building this would
lump all rural counties together and get materially worse results. Pooling is
the *safe* estimator, not a magic one.

**4. Everything hinges on one unmeasured quantity.** Nobody has published ω for
rural EMS temporal demand. If counties respond similarly to season and weather,
pooling wins. If local road networks and demographics dominate, it does not.
**Measuring ω from real NEMSIS data is the single highest-value next step** —
it is a publishable result on its own and it decides whether the method matters.

**5. Where pooling should still win, untested here:** rare-event coefficients
(ice storms, mass gatherings) where even three years yields a handful of
observations, and cold-start counties with no history. Both are worth isolating.

---

## Honest framing for the pitch

Do not claim pooling unlocks rural prediction. Claim this instead:

> We tested our core assumption before building. Pooling helps in the
> lowest-volume counties and protects against the naive alternative, but a
> county with a few hundred calls a year can model itself. The real barrier is
> that no one has built the tool, not that the math doesn't work. Here is the
> operating range we measured, and here is the quantity we need from real data
> to finish the case.

That is a stronger pitch than a rigged win, and it is defensible under
questioning. Judges reward teams that falsified part of their own idea.

---

## Files

- `pooling_experiment.py` — annual volume, empirical-Bayes Poisson-Gamma
- `temporal_pooling.py` — daily structure, per-coefficient James-Stein shrinkage
- `sensitivity_sweep.py` — the ω × volume × history grid
- `sensitivity.csv` — full sweep results
- `fig1_where_pooling_helps.png` — the operating-range map
- `fig2_partial_vs_complete.png` — partial pooling vs naive lumping
- `fig3_low_volume_corner.png` — where the gain actually lives

Reproduce: `python3 sensitivity_sweep.py` (~10 min).
