"""The background picker/Drive jobs.

The point of these jobs is that they survive the browser closing, so what's
tested here is the state machine: a session that is waited on, imported and
reported without any client involvement.
"""

import asyncio

import pytest

from inky_dash import google, google_photos, photos_sync
from inky_dash.config import AppConfig


class FakeDashboard:
    def __init__(self):
        self.config = AppConfig()
        self.invalidated: list[str] = []

    def invalidate(self, module_id):
        self.invalidated.append(module_id)


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("INKY_DASH_DATA", str(tmp_path))
    google.token_path(google.PHOTOS).write_text("{}")
    google.token_path(google.DRIVE).write_text("{}")
    return tmp_path


def session_payload(**over):
    return {
        "session_id": "sess-1",
        "picker_uri": "https://photos.google.com/pick/sess-1",
        "poll_seconds": 0.01,
        "timeout_seconds": 5,
        "ready": False,
        **over,
    }


def stub_picker(monkeypatch, *, polls, result=None, create=None):
    """polls: the sequence of mediaItemsSet values the session reports."""
    remaining = list(polls)
    # The 2s floor exists to keep the Pi from hammering Google; irrelevant here.
    monkeypatch.setattr(photos_sync, "MIN_POLL", 0.001)
    monkeypatch.setattr(google_photos, "create_session", create or (lambda: session_payload()))
    monkeypatch.setattr(
        google_photos, "get_session", lambda sid: {"ready": remaining.pop(0) if remaining else True}
    )
    monkeypatch.setattr(
        google_photos,
        "import_session",
        lambda sid, cap: result or {"imported": 3, "skipped": 0, "duplicates": 0, "total": 3},
    )


async def settle(jobs, until, tries=200):
    """Give the background task a chance to reach a state."""
    for _ in range(tries):
        if until(jobs.status()):
            return jobs.status()
        await asyncio.sleep(0.01)
    raise AssertionError(f"never settled; last status was {jobs.status()}")


# ------------------------------------------------------------------- picker


def test_picking_completes_without_the_client(monkeypatch):
    """The whole point: nobody polls from the browser, and the import still runs."""

    async def scenario():
        dashboard = FakeDashboard()
        jobs = photos_sync.PhotoJobs(dashboard)
        stub_picker(monkeypatch, polls=[False, False, True])

        started = await jobs.start_picker()
        assert started["picker_uri"].endswith("sess-1")
        assert jobs.status()["picker"]["state"] == "waiting"

        status = await settle(jobs, lambda s: s["picker"]["state"] == "done")
        assert status["picker"]["imported"] == 3
        assert dashboard.invalidated == ["photos"]

    asyncio.run(scenario())


def test_a_failed_session_is_reported_not_swallowed(monkeypatch):
    async def scenario():
        jobs = photos_sync.PhotoJobs(FakeDashboard())

        def boom():
            raise google_photos.PickerError("Google Photos is not connected yet.")

        monkeypatch.setattr(google_photos, "create_session", boom)
        assert "not connected" in (await jobs.start_picker())["error"]
        assert jobs.status()["picker"]["state"] == "error"

    asyncio.run(scenario())


def test_an_import_failure_lands_in_the_status(monkeypatch):
    async def scenario():
        jobs = photos_sync.PhotoJobs(FakeDashboard())
        stub_picker(monkeypatch, polls=[True])

        def boom(sid, cap):
            raise google_photos.PickerError("download failed (500)")

        monkeypatch.setattr(google_photos, "import_session", boom)
        await jobs.start_picker()
        status = await settle(jobs, lambda s: s["picker"]["state"] == "error")
        assert "download failed" in status["picker"]["error"]

    asyncio.run(scenario())


def test_cancelling_stops_the_wait(monkeypatch):
    async def scenario():
        jobs = photos_sync.PhotoJobs(FakeDashboard())
        stub_picker(monkeypatch, polls=[False] * 100)
        await jobs.start_picker()
        jobs.cancel_picker()
        assert jobs.status()["picker"]["state"] == "cancelled"
        await asyncio.sleep(0.05)
        assert jobs.status()["picker"]["state"] == "cancelled"  # nothing revived it

    asyncio.run(scenario())


def test_starting_a_second_session_replaces_the_first(monkeypatch):
    async def scenario():
        jobs = photos_sync.PhotoJobs(FakeDashboard())
        stub_picker(monkeypatch, polls=[False] * 100)
        await jobs.start_picker()
        first = jobs._picker_task
        await jobs.start_picker()
        assert first.cancelled() or first.done()

    asyncio.run(scenario())


def test_a_session_nobody_finishes_times_out(monkeypatch):
    async def scenario():
        jobs = photos_sync.PhotoJobs(FakeDashboard())
        monkeypatch.setattr(photos_sync, "MIN_POLL", 0.001)
        monkeypatch.setattr(
            google_photos,
            "create_session",
            lambda: session_payload(poll_seconds=0.01, timeout_seconds=0.02),
        )
        monkeypatch.setattr(google_photos, "get_session", lambda sid: {"ready": False})
        await jobs.start_picker()
        await settle(jobs, lambda s: s["picker"]["state"] == "timeout")

    asyncio.run(scenario())


# -------------------------------------------------------------------- drive


def test_drive_sync_reports_and_refreshes_the_panel(monkeypatch):
    async def scenario():
        dashboard = FakeDashboard()
        dashboard.config.photos.drive_folder = "FOLDER"
        jobs = photos_sync.PhotoJobs(dashboard)
        monkeypatch.setattr(
            photos_sync,
            "sync_folder",
            lambda folder, cap: {"added": 2, "skipped": 0, "pruned": 0, "found": 2, "errors": []},
        )
        result = await jobs.sync_drive_now()
        assert result["added"] == 2
        assert jobs.status()["drive"]["state"] == "done"
        assert dashboard.invalidated == ["photos"]

    asyncio.run(scenario())


def test_a_sync_that_adds_nothing_does_not_disturb_the_display(monkeypatch):
    """An unchanged folder must not wake the render loop — that is the whole
    reason the sync can run hourly without flashing the panel."""

    async def scenario():
        dashboard = FakeDashboard()
        dashboard.config.photos.drive_folder = "FOLDER"
        jobs = photos_sync.PhotoJobs(dashboard)
        monkeypatch.setattr(
            photos_sync,
            "sync_folder",
            lambda folder, cap: {"added": 0, "skipped": 5, "pruned": 0, "found": 5, "errors": []},
        )
        await jobs.sync_drive_now()
        assert dashboard.invalidated == []

    asyncio.run(scenario())


def test_sync_without_a_folder_is_a_no_op():
    async def scenario():
        jobs = photos_sync.PhotoJobs(FakeDashboard())
        assert "No Drive folder" in (await jobs.sync_drive_now())["error"]
        assert jobs.status()["drive"]["state"] == "idle"

    asyncio.run(scenario())


def test_sync_without_a_connection_explains_itself(data_dir):
    async def scenario():
        dashboard = FakeDashboard()
        dashboard.config.photos.drive_folder = "FOLDER"
        google.token_path(google.DRIVE).unlink()
        jobs = photos_sync.PhotoJobs(dashboard)
        assert "not connected" in (await jobs.sync_drive_now())["error"]

    asyncio.run(scenario())


def test_a_drive_url_is_accepted_where_a_folder_id_is_expected(monkeypatch):
    async def scenario():
        dashboard = FakeDashboard()
        dashboard.config.photos.drive_folder = (
            "https://drive.google.com/drive/folders/1AbC-dEf_9?usp=sharing"
        )
        jobs = photos_sync.PhotoJobs(dashboard)
        seen = {}
        monkeypatch.setattr(
            photos_sync,
            "sync_folder",
            lambda folder, cap: (
                seen.setdefault("folder", folder)
                and None
                or {"added": 0, "skipped": 0, "pruned": 0, "found": 0, "errors": []}
            ),
        )
        await jobs.sync_drive_now()
        assert seen["folder"] == "1AbC-dEf_9"

    asyncio.run(scenario())
