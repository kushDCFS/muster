"""
Partial-pooling temporal model — ported from temporal_pooling.py.

Three estimators of a county's daily call rate:
  1. no pooling       - each county fits its own Poisson GLM
  2. complete pooling  - one shared set of temporal coefficients (county intercept only)
  3. partial pooling   - per-coefficient James-Stein shrinkage of a county's own
                         estimate toward the shared one

The statistics are unchanged from the source experiment. Unlike the original
experiment, each county here has its OWN design matrix Z (real weather differs
by county) rather than one shared calendar, so fit_shared/fit_all/
held_out_validation take a dict of per-county Z arrays instead of a single one.
"""

import numpy as np
import pandas as pd
import statsmodels.api as sm

from .real_data import TERMS


def fit_county(Z, y):
    X = sm.add_constant(Z)
    try:
        m = sm.GLM(y, X, family=sm.families.Poisson()).fit()
        return np.asarray(m.params), np.asarray(m.bse)
    except Exception:
        n = X.shape[1]
        return np.full(n, np.nan), np.full(n, np.nan)


def fit_shared(Zs, counts_train, county_ids, n_iter=25):
    """County fixed intercepts + shared temporal coefficients, coordinate
    ascent. `Zs` is {county_id: (n_days, n_terms) array}, each county's own
    real weather-driven design matrix."""
    n_c = len(county_ids)
    n_d = counts_train[county_ids[0]].shape[0]
    n_terms = Zs[county_ids[0]].shape[1]

    y = np.concatenate([counts_train[c] for c in county_ids]).astype(float)
    Zrep = np.concatenate([Zs[c] for c in county_ids], axis=0)
    row_county = np.repeat(np.arange(n_c), n_d)

    totals = np.array([counts_train[c].sum() for c in county_ids], dtype=float)
    alpha = np.log(np.clip(totals, 1, None) / n_d)
    beta = np.zeros(n_terms)

    for _ in range(n_iter):
        offset = alpha[row_county]
        m = sm.GLM(y, Zrep, family=sm.families.Poisson(), offset=offset).fit()
        new_beta = np.asarray(m.params)
        denom = np.array([np.exp(Zs[c] @ new_beta).sum() for c in county_ids])
        new_alpha = np.log(np.clip(totals, 1e-6, None) / denom)
        if np.max(np.abs(new_beta - beta)) < 1e-7:
            alpha, beta = new_alpha, new_beta
            break
        alpha, beta = new_alpha, new_beta

    return alpha, beta


def empirical_bayes(own, own_se, shared):
    """Per-coefficient James-Stein shrinkage toward the shared estimate.
    Returns shrunk coefficients, shrinkage weights, and an approximate
    posterior variance (w_j * se_own_j^2) used downstream for CIs."""
    k = own.shape[1]
    out = np.zeros_like(own)
    weights = np.zeros_like(own)
    post_var = np.zeros_like(own)
    for j in range(k):
        o, se = own[:, j], own_se[:, j]
        ok = np.isfinite(o) & np.isfinite(se) & (se > 0)
        between = np.var(o[ok]) - np.mean(se[ok] ** 2)
        omega2 = max(between, 1e-8)
        w = omega2 / (omega2 + se ** 2)
        w = np.where(ok, w, 0.0)
        out[:, j] = w * np.nan_to_num(o) + (1 - w) * shared[j]
        weights[:, j] = w
        post_var[:, j] = w * np.nan_to_num(se, nan=0.0) ** 2
    return out, weights, post_var


def poisson_deviance(y, mu):
    mu = np.clip(mu, 1e-9, None)
    y_safe = np.where(y > 0, y, 1.0)
    term = np.where(y > 0, y * np.log(y_safe / mu), 0.0)
    return 2 * np.mean(term - (y - mu))


def top_decile_lift(y, mu):
    n = len(y)
    k = max(int(0.10 * n), 1)
    idx = np.argsort(mu)[::-1][:k]
    base = y.mean()
    return (y[idx].mean() / base) if base > 0 else np.nan


def _profile_intercept(Z, beta, total_count):
    """ML intercept for a fixed beta: alpha = log(total_count / sum(exp(Z@beta))).
    Used to re-calibrate the partial-pooling intercept against the ACTUAL
    shrunk beta for that county, rather than reusing the complete-pooling
    intercept (which was profiled against a different, shared beta -- fine
    when counties barely differ from the shared curve, but a real bias when
    a county's own coefficients differ substantially, as happens with real,
    heterogeneous counties)."""
    denom = np.exp(Z @ beta).sum()
    return np.log(np.clip(total_count, 1e-6, None) / denom)


def fit_all(calendars, counts, county_ids, train_days):
    """Fit the model on all observed history. `calendars` is {county_id ->
    DataFrame with TERMS columns}, `counts` is {county_id -> array of daily
    counts}. Returns per-county coefficients (own, shared, shrunk), shrinkage
    weights, posterior variances, and the shared/county/shrunk intercepts."""
    Zs = {c: calendars[c][TERMS].values[:train_days] for c in county_ids}
    n_c = len(county_ids)

    own = np.zeros((n_c, len(TERMS) + 1))
    own_se = np.zeros((n_c, len(TERMS) + 1))
    for i, c in enumerate(county_ids):
        p, s = fit_county(Zs[c], counts[c])
        own[i], own_se[i] = p, s

    intercepts, shared_beta = fit_shared(Zs, counts, county_ids)
    eb, weights, post_var = empirical_bayes(own[:, 1:], own_se[:, 1:], shared_beta)

    shrunk_intercept = np.array([
        _profile_intercept(Zs[c], eb[i], counts[c].sum()) for i, c in enumerate(county_ids)
    ])

    idx = {c: i for i, c in enumerate(county_ids)}
    return {
        "county_index": idx,
        "own_intercept": own[:, 0],
        "own_beta": own[:, 1:],
        "own_se": own_se[:, 1:],
        "shared_intercept": intercepts,
        "shared_beta": shared_beta,
        "shrunk_beta": eb,
        "shrunk_intercept": shrunk_intercept,
        "shrink_weight": weights,
        "shrunk_post_var": post_var,
    }


def held_out_validation(calendars, counts, county_ids, train_days, split_days=365 * 2):
    """Refit on the first `split_days` and score against the remainder, so the
    validation numbers reflect genuine held-out performance rather than
    in-sample fit. Mirrors temporal_pooling.py's experiment design."""
    Zs_tr = {c: calendars[c][TERMS].values[:split_days] for c in county_ids}
    Zs_te = {c: calendars[c][TERMS].values[split_days:train_days] for c in county_ids}
    counts_tr = {c: counts[c][:split_days] for c in county_ids}
    counts_te = {c: counts[c][split_days:train_days] for c in county_ids}

    n_c = len(county_ids)
    own = np.zeros((n_c, len(TERMS) + 1))
    own_se = np.zeros((n_c, len(TERMS) + 1))
    for i, c in enumerate(county_ids):
        p, s = fit_county(Zs_tr[c], counts_tr[c])
        own[i], own_se[i] = p, s

    intercepts, shared_beta = fit_shared(Zs_tr, counts_tr, county_ids)
    eb, weights, _ = empirical_bayes(own[:, 1:], own_se[:, 1:], shared_beta)
    shrunk_intercepts = np.array([
        _profile_intercept(Zs_tr[c], eb[i], counts_tr[c].sum()) for i, c in enumerate(county_ids)
    ])

    rows = []
    for i, c in enumerate(county_ids):
        Zte = Zs_te[c]
        mu_none = np.exp(own[i, 0] + Zte @ own[i, 1:])
        mu_full = np.exp(intercepts[i] + Zte @ shared_beta)
        mu_part = np.exp(shrunk_intercepts[i] + Zte @ eb[i])
        y = counts_te[c].astype(float)
        rows.append({
            "county_id": c,
            "annual_calls": counts_tr[c].sum() / (split_days / 365),
            "dev_none": poisson_deviance(y, mu_none),
            "dev_full": poisson_deviance(y, mu_full),
            "dev_partial": poisson_deviance(y, mu_part),
            "lift_none": top_decile_lift(y, mu_none),
            "lift_full": top_decile_lift(y, mu_full),
            "lift_partial": top_decile_lift(y, mu_part),
            "mean_shrink_w": weights[i].mean(),
        })
    return pd.DataFrame(rows)
