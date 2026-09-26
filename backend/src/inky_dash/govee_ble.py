"""Passive BLE listener for Govee H5075 temperature/humidity advertisements.

The H5075 needs no connection: it encodes readings in the manufacturer data
(company id 0xEC88) of its BLE advertisements. Payload layout (after bleak strips
the company id):

    byte 0        flags/padding (0x00)
    bytes 1-3     big-endian packed value: temp_decidegrees * 1000 + humidity_decipercent
                  (top bit of byte 1 set = negative temperature)
    byte 4        battery percent
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from inky_dash.storage import Reading, SensorStore

GOVEE_MANUFACTURER_ID = 0xEC88

log = logging.getLogger(__name__)


def parse_h5075(payload: bytes) -> tuple[float, float, int | None] | None:
    """Return (temp_c, humidity_percent, battery_percent) or None if not parseable."""
    if len(payload) < 4:
        return None
    packed = int.from_bytes(payload[1:4], "big")
    negative = bool(packed & 0x800000)
    if negative:
        packed ^= 0x800000
    temp_c = (packed // 1000) / 10
    if negative:
        temp_c = -temp_c
    humidity = (packed % 1000) / 10
    battery = payload[4] if len(payload) > 4 else None
    if humidity > 100 or not -40 <= temp_c <= 85:
        return None
    return temp_c, humidity, battery


class GoveeListener:
    """Continuously scans for H5075 advertisements and logs throttled readings."""

    def __init__(self, dashboard, store: SensorStore | None = None):
        self._dashboard = dashboard
        self._store = store or SensorStore()
        self._last_logged = 0.0

    def _on_advertisement(self, device, advertisement) -> None:
        payload = advertisement.manufacturer_data.get(GOVEE_MANUFACTURER_ID)
        name = advertisement.local_name or ""
        if payload is None or not (name.startswith("GVH5075") or len(payload) in (6, 7)):
            return
        wanted = self._dashboard.config.govee.mac
        if wanted and device.address.lower() != wanted.lower():
            return
        parsed = parse_h5075(payload)
        if parsed is None:
            return
        temp_c, humidity, battery = parsed
        interval = self._dashboard.config.govee.log_interval_seconds
        if time.monotonic() - self._last_logged < interval:
            return
        self._last_logged = time.monotonic()
        self._store.insert(
            Reading(
                ts=datetime.now(UTC),
                temp_c=temp_c,
                humidity=humidity,
                battery=battery,
                address=device.address,
            )
        )
        log.debug("govee reading %.1fC %.1f%% batt=%s", temp_c, humidity, battery)

    def _wanted(self) -> bool:
        return "govee" in self._dashboard.active_module_ids()

    async def run(self) -> None:
        from bleak import BleakScanner  # lazy: importable even where BLE is unavailable

        # Self-gating on the active layout (re-checked, not just at startup) so
        # adding or removing the govee panel in the UI starts/stops scanning.
        while True:
            try:
                if not self._wanted():
                    await asyncio.sleep(30)
                    continue
                scanner = BleakScanner(detection_callback=self._on_advertisement)
                async with scanner:
                    while self._wanted():
                        await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("BLE scanning unavailable (%s); retrying in 60s", exc)
                await asyncio.sleep(60)
