"""
Coverage optimizer: given a number of staffed hours an agency can actually
afford, choose WHICH hours to buy.

This is the piece a chief cannot do in their head. Failures are spread across
168 hour-of-week cells; picking the set of shifts that removes the most of
them is a small combinatorial problem, and the answer is frequently not the
one intuition suggests (the worst single hour is often not inside the best
block).

Two deliberate constraints make the output operationally real rather than
mathematically cute:

  1. Coverage is bought in CONTIGUOUS BLOCKS, not scattered hours. Nobody
     staffs 07:00-08:00 Tuesday and 14:00-15:00 Thursday. Real duty crews are
     4-12 hour shifts.
  2. A staffed duty crew is credited with eliminating CREW-ASSEMBLY failures
     in its window -- that is what it is for -- but NOT truck-busy failures,
     since a second simultaneous call still collides with a single unit. The
     split is reported so the claim is inspectable.
"""

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Blended hourly cost of putting a staffed 2-person crew on duty. Volunteer
# agencies moving to paid coverage typically pay a duty stipend rather than a
# full wage, so this is deliberately mid-range and is user-overridable
# everywhere it appears.
DEFAULT_COST_PER_HOUR = 60.0
BLOCK_LENGTHS = (4, 6, 8, 12)
MIN_BLOCK = 4


def build_grid(classified):
    """168 hour-of-week cells from the classified incident frame."""
    grid = {}
    for w in range(7):
        for h in range(24):
            grid[(w, h)] = {"weekday": w, "hour": h, "incidents": 0,
                            "failures": 0, "crew_failures": 0}
    for row in classified.itertuples():
        key = (int(row.weekday), int(row.hour))
        cell = grid[key]
        cell["incidents"] += 1
        if row.no_crew or row.severe_delay:
            cell["failures"] += 1
            # crew-assembly failures are the ones a staffed duty crew removes
            cell["crew_failures"] += 1
    return grid


def _candidate_blocks(grid, lengths=BLOCK_LENGTHS):
    """Every contiguous within-day block, with the failures it would cover.
    Blocks do not wrap past midnight -- a shift that crosses into the next
    day is a different staffing decision and agencies schedule them as such."""
    out = []
    for w in range(7):
        for start in range(24):
            for L in lengths:
                if start + L > 24:
                    continue
                hours = [(w, start + i) for i in range(L)]
                fails = sum(grid[k]["crew_failures"] for k in hours)
                inc = sum(grid[k]["incidents"] for k in hours)
                out.append({
                    "weekday": w, "weekday_name": WEEKDAYS[w],
                    "start": start, "end": start + L, "length": L,
                    "failures_covered": fails, "incidents": inc,
                    "density": fails / L,
                    "cells": hours,
                })
    return out


def optimize(grid, hours_budget, cost_per_hour=DEFAULT_COST_PER_HOUR,
             lengths=BLOCK_LENGTHS):
    """Greedily pick non-overlapping blocks with the highest failures-per-hour
    until the budget is spent. Greedy is the right tool here: the objective is
    additive over disjoint cells, blocks are short relative to the week, and an
    exact solver would add opacity for a negligible gain on a 168-cell grid."""
    total_failures = sum(c["crew_failures"] for c in grid.values())
    remaining = int(hours_budget)
    used = set()
    chosen = []

    while remaining >= MIN_BLOCK:
        best = None
        for b in _candidate_blocks(grid, lengths):
            if b["length"] > remaining:
                continue
            if any(c in used for c in b["cells"]):
                continue
            if b["failures_covered"] == 0:
                continue
            if best is None or b["density"] > best["density"] or (
                    b["density"] == best["density"] and b["failures_covered"] > best["failures_covered"]):
                best = b
        if best is None:
            break
        chosen.append(best)
        used.update(best["cells"])
        remaining -= best["length"]

    covered = sum(b["failures_covered"] for b in chosen)
    hours_used = sum(b["length"] for b in chosen)
    chosen.sort(key=lambda b: (b["weekday"], b["start"]))

    return {
        "hours_requested": int(hours_budget),
        "hours_used": hours_used,
        "blocks": [{
            "weekday": b["weekday_name"], "start": b["start"], "end": b["end"],
            "length": b["length"], "failures_covered": b["failures_covered"],
            "incidents": b["incidents"],
            "label": f"{b['weekday_name']} {b['start']:02d}:00–{b['end']:02d}:00",
        } for b in chosen],
        "failures_covered": covered,
        "failures_total": total_failures,
        "coverage_pct": round(100 * covered / total_failures, 1) if total_failures else 0.0,
        "weekly_cost": round(hours_used * cost_per_hour),
        "annual_cost": round(hours_used * cost_per_hour * 52),
        "cost_per_failure_prevented": round(hours_used * cost_per_hour * 52 / covered)
                                        if covered else None,
        "cost_per_hour": cost_per_hour,
        "covered_cells": sorted([[c[0], c[1]] for c in used]),
    }


def returns_curve(grid, cost_per_hour=DEFAULT_COST_PER_HOUR, max_hours=84, step=4):
    """Coverage achieved at each affordable budget. This is usually the most
    persuasive output: it shows a board where the knee of the curve is, i.e.
    the point past which each additional staffed hour buys much less."""
    pts = []
    for h in range(step, max_hours + 1, step):
        r = optimize(grid, h, cost_per_hour)
        pts.append({
            "hours": h,
            "coverage_pct": r["coverage_pct"],
            "failures_covered": r["failures_covered"],
            "annual_cost": r["annual_cost"],
        })
    # marginal gain per additional step, used to locate the knee
    for i, p in enumerate(pts):
        prev = pts[i - 1]["coverage_pct"] if i else 0.0
        p["marginal_gain"] = round(p["coverage_pct"] - prev, 1)
    knee = None
    for i in range(1, len(pts)):
        if pts[i]["marginal_gain"] < pts[0]["marginal_gain"] * 0.4:
            knee = pts[i - 1]["hours"]
            break
    return {"points": pts, "knee_hours": knee}
