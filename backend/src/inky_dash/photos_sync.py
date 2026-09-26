"""Background photo work: the Google Photos picking session and the Drive sync.

Both of these outlive a browser tab, which is the whole point of running them
here. Picking in particular used to be driven from the config UI's event loop —
close the laptop mid-pick and the import never happened, while Google's ~60
minute download URLs quietly expired and took the selection with them. The
server now owns the wait, so the UI is free to be a status display that anyone
can reload and rejoin.

One job of each kind at a time: this is a photo frame with one owner, and
concurrent imports would only race over the pool.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

from inky_dash import google, google_drive, google_photos, photos_store

log = logging.getLogger(__name__)

# How long the Drive loop sleeps when nothing is configured, before looking again
# (a folder can be set in the UI at any moment).
IDLE_POLL = 60.0
MIN_POLL = 2.0


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PhotoJobs:
    """Owns the picker task and the Drive sync loop for one dashboard."""

    def __init__(self, dashboard: Any):
        self.dashboard = dashboard
        self._picker_task: asyncio.Task | None = None
        self._picker: dict[str, Any] = {"state": "idle"}
        self._drive: dict[str, Any] = {"state": "idle"}
        self._drive_lock = asyncio.Lock()
        self._wake_drive = asyncio.Event()

    # ------------------------------------------------------------- reporting

    def status(self) -> dict[str, Any]:
        """Everything the config UI polls for while work is in flight."""
        return {"picker": dict(self._picker), "drive": dict(self._drive)}

    # ---------------------------------------------------------------- picker

    async def start_picker(self) -> dict[str, Any]:
        """Open a picking session and start waiting for it in the background."""
        self.cancel_picker()
        try:
            session = await asyncio.to_thread(google_photos.create_session)
        except google_photos.PickerError as exc:
            self._picker = {"state": "error", "error": str(exc), "at": _now()}
            return {"error": str(exc)}

        self._picker = {
            "state": "waiting",
            "picker_uri": session["picker_uri"],
            "session_id": session["session_id"],
            "started_at": _now(),
        }
        self._picker_task = asyncio.create_task(self._run_picker(session))
        return {"picker_uri": session["picker_uri"], "state": "waiting"}

    def cancel_picker(self) -> None:
        if self._picker_task and not self._picker_task.done():
            self._picker_task.cancel()
        self._picker_task = None
        if self._picker.get("state") in {"waiting", "importing"}:
            self._picker = {"state": "cancelled", "at": _now()}

    async def _run_picker(self, session: dict[str, Any]) -> None:
        """Poll until the user presses Done, then import straight away."""
        session_id = session["session_id"]
        interval = max(MIN_POLL, session["poll_seconds"])
        deadline = time.monotonic() + session["timeout_seconds"]
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(interval)
                poll = await asyncio.to_thread(google_photos.get_session, session_id)
                if not poll.get("ready"):
                    continue

                self._picker = {**self._picker, "state": "importing"}
                result = await asyncio.to_thread(
                    google_photos.import_session,
                    session_id,
                    self.dashboard.config.photos.max_photos,
                )
                self._picker = {"state": "done", "at": _now(), **result}
                self.dashboard.invalidate("photos")
                return
            self._picker = {"state": "timeout", "at": _now()}
        except asyncio.CancelledError:
            raise
        except google_photos.PickerError as exc:
            self._picker = {"state": "error", "error": str(exc), "at": _now()}
        except Exception as exc:  # a background task must never die silently
            log.exception("picker session failed")
            self._picker = {"state": "error", "error": str(exc), "at": _now()}

    # ----------------------------------------------------------------- drive

    def wake_drive(self) -> None:
        self._wake_drive.set()

    async def sync_drive_now(self) -> dict[str, Any]:
        """Sync on demand (the UI's button). Shares the lock with the loop, so a
        button press during a scheduled sync waits rather than racing it."""
        async with self._drive_lock:
            return await self._sync_drive()

    async def _sync_drive(self) -> dict[str, Any]:
        config = self.dashboard.config.photos
        folder = google_drive.parse_folder_id(config.drive_folder or "")
        if not folder:
            self._drive = {"state": "idle"}
            return {"error": "No Drive folder set."}
        if not google.is_connected(google.DRIVE):
            self._drive = {"state": "error", "error": "Google Drive is not connected yet."}
            return {"error": self._drive["error"]}

        self._drive = {**self._drive, "state": "syncing"}
        try:
            result = await asyncio.to_thread(sync_folder, folder, config.max_photos)
        except google_drive.DriveError as exc:
            self._drive = {"state": "error", "error": str(exc), "at": _now()}
            return {"error": str(exc)}
        except Exception as exc:
            log.exception("drive sync failed")
            self._drive = {"state": "error", "error": str(exc), "at": _now()}
            return {"error": str(exc)}

        self._drive = {"state": "done", "at": _now(), **result}
        if result["added"] or result["pruned"]:
            self.dashboard.invalidate("photos")
        return result

    async def run_drive_loop(self) -> None:
        """Long-running task started in the app lifespan. Self-gating: it costs a
        single check per minute while no folder is configured."""
        while True:
            config = self.dashboard.config.photos
            configured = bool(google_drive.parse_folder_id(config.drive_folder or ""))
            if configured and google.is_connected(google.DRIVE):
                async with self._drive_lock:
                    with contextlib.suppress(Exception):
                        await self._sync_drive()
                wait = config.drive_sync_minutes * 60
            else:
                wait = IDLE_POLL
            self._wake_drive.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake_drive.wait(), timeout=wait)


def sync_folder(folder: str, max_photos: int) -> dict[str, Any]:
    """Indirection so tests can stub the network half without touching asyncio."""
    return google_drive.sync_folder(folder, max_photos)


def pool_summary() -> dict[str, Any]:
    entries = photos_store.load_index()
    return {"count": len(entries), "bytes": photos_store.total_bytes()}
