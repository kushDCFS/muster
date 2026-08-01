"""
Rural EMS coverage forecasting prototype.

Run with:
    python -m uvicorn app:main --reload

County names, population, land area, and the 3-year+forecast weather calendar
are REAL (see model/real_data.py and data/DATA_SOURCES.md). Daily/hourly call
counts, transport durations, and block-group prevention data are still
MODELED — no public per-county daily EMS dispatch log exists to fit on
directly. See data/FINDINGS.md for the pooling-method background.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routes import router as api_router, build_usecases
from api.audit_routes import router as audit_router
from model.fit import Fitted


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup takes ~15s warm and up to ~90s on a first run, dominated by
    # importing statsmodels/scipy and compiling bytecode. Without progress
    # output that reads as a hang, so we narrate it.
    import time
    t0 = time.time()
    print("[1/2] Fitting partial-pooling model across 19 counties...", flush=True)
    fitted = Fitted()
    print(f"      done in {time.time()-t0:.0f}s", flush=True)
    t1 = time.time()
    print("[2/2] Warming cross-county rankings...", flush=True)
    # warm the cross-county ranking now so the Use cases tab is instant in a
    # live demo instead of paying a 10-15s wait on first open
    fitted._usecase_cache = build_usecases(fitted)
    print(f"      done in {time.time()-t1:.0f}s", flush=True)
    print(f"Ready in {time.time()-t0:.0f}s -> http://localhost:8000", flush=True)
    app.state.fitted = fitted
    yield


main = FastAPI(title="Rural EMS Coverage Forecast (prototype)", lifespan=lifespan)
main.include_router(api_router)
main.include_router(audit_router)
main.mount("/", StaticFiles(directory="static", html=True), name="static")
