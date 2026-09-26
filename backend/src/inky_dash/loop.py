"""Dashboard state and the render loop that drives the display."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

from PIL import Image

from inky_dash.config import AppConfig, PlaylistItem, load_config, save_config
from inky_dash.display import create_display
from inky_dash.layout import find_layout, resolve_layout
from inky_dash.modules import MODULES
from inky_dash.render import compose

log = logging.getLogger(__name__)

# A tick that lands a hair early must still count the dwell as spent, or the
# screen would linger for a whole extra refresh cycle.
DWELL_SLACK = 1.0
MIN_WAIT = 1.0  # floor on the loop's sleep, so a due screen can never busy-loop


def module_ttl(config: AppConfig, module_id: str, default: float) -> float:
    """Seconds a module's fetch stays fresh: config-driven for the network-backed
    modules, the module's own default for local reads (clock, govee)."""
    minutes = {
        "weather": config.fetch.weather_minutes,
        "calendar": config.fetch.calendar_minutes,
        "economist": config.fetch.economist_minutes,
        "xkcd": config.fetch.xkcd_minutes,
        "vox": config.fetch.vox_minutes,
    }.get(module_id)
    return minutes * 60 if minutes is not None else default


class Dashboard:
    def __init__(self, config: AppConfig | None = None):
        self.config = config or load_config()
        self.display = create_display(self.config.display)
        self.data: dict[str, dict[str, Any]] = {}
        self.status: dict[str, dict[str, Any]] = {}
        self.last_render: datetime | None = None
        self._fetched_at: dict[str, float] = {}
        self._wake = asyncio.Event()
        # A Spectra refresh is ~30s of visible flashing, so identical frames are
        # never re-shown; the hash of the last frame put on the panel detects that.
        self._shown_hash: bytes | None = None
        self._force_show = False
        # Playlist rotation: which screen is up and when it went up (monotonic).
        # None = nothing shown yet, so the next tick starts the current screen's dwell.
        self._screen_index = 0
        self._screen_started: float | None = None

    def playlist_screens(self) -> list[PlaylistItem]:
        """The screens the rotation actually cycles through: enabled items whose
        layout still exists. Empty means single-layout mode (layout.active) — a
        playlist that is off, empty, or entirely dangling never blanks the panel."""
        if not self.config.playlist.enabled:
            return []
        return [
            item
            for item in self.config.playlist.items
            if item.enabled and find_layout(self.config.layout, item.layout) is not None
        ]

    def current_screen(self) -> PlaylistItem | None:
        screens = self.playlist_screens()
        return screens[self._screen_index % len(screens)] if screens else None

    def current_layout_id(self) -> str:
        """The layout on the panel right now: the current playlist screen, or the
        configured active layout when no playlist is running."""
        screen = self.current_screen()
        return screen.layout if screen is not None else self.config.layout.active

    def active_module_ids(self) -> list[str]:
        layout = resolve_layout(self.config.layout, self.current_layout_id())
        seen: list[str] = []
        for region in layout.regions:
            if region.module is not None and region.module not in seen:
                seen.append(region.module)
        return seen

    def playlist_status(self) -> dict[str, Any] | None:
        """What the UI shows about the rotation; None when no playlist is running."""
        screens = self.playlist_screens()
        if not screens:
            return None
        index = self._screen_index % len(screens)
        screen = screens[index]
        elapsed = 0.0 if self._screen_started is None else time.monotonic() - self._screen_started
        return {
            "position": index + 1,
            "count": len(screens),
            "layout": screen.layout,
            "name": resolve_layout(self.config.layout, screen.layout).name,
            "minutes": screen.minutes,
            "seconds_remaining": max(0, round(screen.minutes * 60 - elapsed)),
        }

    def _advance_if_due(self) -> None:
        """Move to the next screen once the current one has had its dwell time.
        Runs at the top of every tick; the loop sizes its sleep so a tick lands
        as the dwell expires."""
        screens = self.playlist_screens()
        if not screens:
            self._screen_started = None
            return
        now = time.monotonic()
        self._screen_index %= len(screens)  # the list may have shrunk under us
        if self._screen_started is None:
            self._screen_started = now
        elif now - self._screen_started >= screens[self._screen_index].minutes * 60 - DWELL_SLACK:
            self._screen_index = (self._screen_index + 1) % len(screens)
            self._screen_started = now

    def next_screen(self) -> None:
        """Skip to the next screen now (the UI's 'next screen' button). Forced, so
        the panel updates even if the two screens happen to render identically."""
        screens = self.playlist_screens()
        if screens:
            self._screen_index = (self._screen_index + 1) % len(screens)
            self._screen_started = time.monotonic()
        self.force_refresh()

    def _wait_seconds(self) -> float:
        """How long to sleep before the next tick: the data-refresh cadence, cut
        short so a tick lands when the current playlist screen expires."""
        timeout = float(self.config.refresh_seconds)
        screen = self.current_screen()
        if screen is not None and self._screen_started is not None:
            remaining = screen.minutes * 60 - (time.monotonic() - self._screen_started)
            timeout = min(timeout, max(remaining, MIN_WAIT))
        return timeout

    async def ensure_data(self, config: AppConfig, module_ids: list[str]) -> None:
        """Fetch any of module_ids whose cached data is missing or past its TTL.
        Shared by the render loop and the candidate-preview endpoint."""
        for module_id in module_ids:
            module = MODULES.get(module_id)
            if module is None:
                continue
            age = time.monotonic() - self._fetched_at.get(module_id, float("-inf"))
            if module_id in self.data and age < module_ttl(config, module_id, module.ttl):
                continue
            try:
                self.data[module_id] = await module.fetch(config)
                self._fetched_at[module_id] = time.monotonic()
                self.status[module_id] = {"ok": True, "fetched_at": datetime.now(UTC).isoformat()}
            except Exception as exc:  # keep stale data; record the failure
                log.exception("fetch failed for module %s", module_id)
                self.status[module_id] = {
                    "ok": False,
                    "error": str(exc),
                    "failed_at": datetime.now(UTC).isoformat(),
                }

    async def refresh_data(self) -> None:
        await self.ensure_data(self.config, self.active_module_ids())

    def render_frame(self, layout_id: str | None = None) -> Image.Image:
        return compose(
            self.config,
            self.data,
            self.display.resolution,
            layout_id or self.current_layout_id(),
        )

    def wake(self) -> None:
        self._wake.set()

    def force_refresh(self) -> None:
        """Refresh the panel even if the frame is unchanged — the explicit
        'refresh now' button doubles as a way to clear ghosting."""
        self._force_show = True
        self.wake()

    def invalidate(self, module_id: str) -> None:
        """Force a module's next fetch and wake the loop — for state that changes
        outside the config, like photos being added to or removed from the pool."""
        self._fetched_at.pop(module_id, None)
        self.wake()

    def update_config(self, config: AppConfig) -> None:
        old, self.config = self.config, config
        save_config(config)
        if config.display != old.display:
            self.display = create_display(config.display)
            self._shown_hash = None  # a new display has nothing on it yet
        # Layout-only edits keep cached data: newly-placed modules still fetch
        # (absent from _fetched_at) but nothing else re-hits its external API.
        # Fetch-interval edits keep it too — the new interval simply applies to
        # the existing timestamps rather than forcing a refetch of everything.
        unaffected = {"layout", "playlist", "display", "refresh_seconds", "fetch"}
        if config.model_dump(exclude=unaffected) != old.model_dump(exclude=unaffected):
            self._fetched_at.clear()  # module settings changed; refetch everything
        if config.playlist != old.playlist:
            # Restart the rotation so a save is predictable: the edited playlist
            # begins at its first screen rather than mid-cycle at a stale index.
            self._screen_index = 0
            self._screen_started = None
        self.wake()

    async def _tick(self) -> None:
        """One loop iteration: advance the playlist if the current screen has had
        its time, then fetch, compose, and show — unless the composed frame is
        pixel-identical to what the panel already displays. A Spectra refresh is
        ~30s of visible flashing, so unchanged frames are skipped."""
        self._advance_if_due()
        await self.refresh_data()
        frame = self.render_frame()
        digest = hashlib.sha1(frame.tobytes()).digest()
        if not self._force_show and digest == self._shown_hash:
            log.debug("frame unchanged; skipping display refresh")
            return
        # display.show blocks ~30s on real hardware; keep the event loop free
        await asyncio.get_running_loop().run_in_executor(None, self.display.show, frame)
        self._shown_hash = digest
        self._force_show = False
        self.last_render = datetime.now(UTC)

    async def run(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self._tick()
            except Exception:
                log.exception("render loop iteration failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._wait_seconds())
