"""Audit endpoints: upload a dispatch export, get findings, render the report."""

import io
import uuid

import pandas as pd
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, PlainTextResponse

from model import audit as audit_model
from model import optimizer as opt
from model import benchmark as bench
from api.report import render_report
from api.packet import render_packet

router = APIRouter(prefix="/api/audit")

# In-memory only, keyed by a random id. Nothing is written to disk and nothing
# survives a restart -- deliberate for a prototype handling agency data.
_AUDITS = {}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _store(payload):
    aid = uuid.uuid4().hex[:12]
    _AUDITS[aid] = payload
    if len(_AUDITS) > 50:                      # crude cap, oldest out
        for k in list(_AUDITS)[: len(_AUDITS) - 50]:
            _AUDITS.pop(k, None)
    return aid


@router.get("/template.csv", response_class=PlainTextResponse)
def template_csv():
    """The exact file we ask an agency to send."""
    header = ",".join(c[0] for c in audit_model.CSV_TEMPLATE_COLUMNS)
    example = ("INC-1001,2026-03-14 14:22:00,2026-03-14 14:31:00,2026-03-14 14:52:00,"
               "2026-03-14 16:05:00,MEDIC-1,Transported,N")
    example2 = ("INC-1002,2026-03-14 15:40:00,,,,,Mutual Aid - No Unit Available,Y")
    return f"{header}\n{example}\n{example2}\n"


@router.get("/fields")
def fields():
    return {
        "columns": [{"name": n, "meaning": m, "requirement": r}
                    for n, m, r in audit_model.CSV_TEMPLATE_COLUMNS],
        "accepted_aliases": audit_model.COLUMN_ALIASES,
        "privacy": (
            "Columns whose names look like patient data (name, DOB, address, complaint, "
            "vitals, insurance, age, sex, race, narrative, MRN...) are dropped at parse time "
            "and never analyzed. Only timestamps, unit ids, and disposition codes are read. "
            "Everything reported is an aggregate count or a time-of-day pattern."
        ),
    }


@router.post("/upload")
async def upload(file: UploadFile = File(...),
                 agency_name: str = Form("Your agency"),
                 mutual_aid_fee: float = Form(350)):
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (limit 20 MB).")
    try:
        df, parse_report = audit_model.parse_upload(raw, file.filename or "")
        findings = audit_model.analyze(df, mutual_aid_fee=mutual_aid_fee)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Could not analyze file: {e}")

    svc = findings.get("service_type", "ems")
    bench.contribute(findings, svc)          # de-identified: 4 aggregate numbers, no identity
    payload = {
        "agency_name": agency_name.strip() or "Your agency",
        "source": file.filename or "uploaded file",
        "is_demo": False,
        "parse_report": parse_report,
        "findings": findings,
        "benchmark": bench.compare(findings, svc),
    }
    return {"audit_id": _store(payload), **payload}


@router.post("/reference")
def reference():
    """Run the audit on the bundled REAL San Francisco dispatch export.
    Serves as both a live demonstration and a control: a career-staffed
    urban system should show almost no crew-assembly failure, and does."""
    meta = audit_model.REFERENCE_META
    df, parse_report = audit_model.parse_upload(
        audit_model.load_reference_export(), "sf_ems_2024_real.csv")
    findings = audit_model.analyze(df)
    payload = {
        "agency_name": meta["agency_name"],
        "source": meta["source"],
        "is_demo": False,
        "is_reference": True,
        "reference_meta": meta,
        "parse_report": parse_report,
        "findings": findings,
        "benchmark": bench.compare(findings, findings.get("service_type", "ems")),
    }
    return {"audit_id": _store(payload), **payload}


@router.get("/benchmark/cohort")
def cohort_status():
    """What the shared dataset currently contains. Deliberately exposed: the
    cohort's size is the honest measure of how far this moat has been built."""
    rows = bench._load()
    by = {}
    for r in rows:
        by.setdefault(f"{r['service_type']} / {r['band']}", 0)
        by[f"{r['service_type']} / {r['band']}"] += 1
    return {"total_rows": len(rows), "min_for_percentile": 8, "by_cohort": by,
            "stored_per_row": ["service_type", "volume_band", "failure_rate",
                               "median_assembly_min", "workday_penalty"],
            "not_stored": ["agency name", "location", "any incident", "any person"]}


@router.get("/{audit_id}")
def get_audit(audit_id: str):
    a = _AUDITS.get(audit_id)
    if not a:
        raise HTTPException(404, "Audit not found (in-memory only; it may have expired).")
    return {"audit_id": audit_id, **a}


def _grid_from(payload):
    """Rebuild the optimizer's dict-keyed grid from the stored findings."""
    return {(g["weekday"], g["hour"]): {**g, "crew_failures": g["failures"]}
            for g in payload["findings"]["grid"]}


@router.get("/{audit_id}/optimize")
def optimize(audit_id: str, hours: int = 20, cost_per_hour: float = 60.0):
    a = _AUDITS.get(audit_id)
    if not a:
        raise HTTPException(404, "Audit not found (in-memory only; it may have expired).")
    grid = _grid_from(a)
    hours = max(4, min(int(hours), 168))
    result = opt.optimize(grid, hours, cost_per_hour)
    curve = opt.returns_curve(grid, cost_per_hour)
    return {
        "audit_id": audit_id, "agency_name": a["agency_name"],
        "plan": result, "curve": curve,
        "assumptions": (
            f"Assumes a staffed duty crew eliminates crew-assembly failures inside its window "
            f"(what it is for) but not simultaneous-call collisions. Coverage is bought in "
            f"contiguous blocks of at least {opt.MIN_BLOCK} hours. Cost assumes "
            f"${cost_per_hour:.0f}/hour for a staffed 2-person crew -- change it to your real "
            "stipend or wage rate."
        ),
    }


@router.get("/{audit_id}/packet", response_class=HTMLResponse)
def packet(audit_id: str, hours: int = 20, cost_per_hour: float = 60.0):
    a = _AUDITS.get(audit_id)
    if not a:
        raise HTTPException(404, "Audit not found (in-memory only; it may have expired).")
    grid = _grid_from(a)
    plan = opt.optimize(grid, max(4, min(int(hours), 168)), cost_per_hour)
    return HTMLResponse(render_packet(audit_id, a, plan))


@router.get("/{audit_id}/report", response_class=HTMLResponse)
def report(audit_id: str):
    a = _AUDITS.get(audit_id)
    if not a:
        raise HTTPException(404, "Audit not found (in-memory only; it may have expired).")
    return HTMLResponse(render_report(audit_id, a))
