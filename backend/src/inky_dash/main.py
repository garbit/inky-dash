from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from inky_dash.api import router
from inky_dash.govee_ble import GoveeListener
from inky_dash.loop import Dashboard
from inky_dash.photos_sync import PhotoJobs

FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(level=logging.INFO)
    dashboard = Dashboard()
    app.state.dashboard = dashboard
    jobs = PhotoJobs(dashboard)
    app.state.photo_jobs = jobs
    # Both background workers self-gate on config, so start them unconditionally —
    # a layout edit that adds the govee panel must begin scanning without a
    # restart, and the same goes for setting a Drive folder.
    tasks = [
        asyncio.create_task(dashboard.run()),
        asyncio.create_task(GoveeListener(dashboard).run()),
        asyncio.create_task(jobs.run_drive_loop()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


def create_app() -> FastAPI:
    app = FastAPI(title="inky-dash", lifespan=lifespan)
    app.include_router(router)  # must come before the catch-all static mount
    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("inky_dash.main:app", host="0.0.0.0", port=8000)
