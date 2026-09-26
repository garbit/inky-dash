"""SQLite log of Govee sensor readings, used for on-screen trend graphs."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from inky_dash.config import data_dir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sensor_readings (
    ts TEXT NOT NULL,
    temp_c REAL NOT NULL,
    humidity REAL NOT NULL,
    battery INTEGER,
    address TEXT
);
CREATE INDEX IF NOT EXISTS idx_sensor_readings_ts ON sensor_readings (ts);
"""


@dataclass
class Reading:
    ts: datetime
    temp_c: float
    humidity: float
    battery: int | None = None
    address: str | None = None


class SensorStore:
    """Opens a connection per operation: cheap at our write rates and thread/task safe."""

    def __init__(self, path: Path | None = None):
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or data_dir() / "inky-dash.db"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.executescript(_SCHEMA)
        return conn

    def insert(self, reading: Reading) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sensor_readings (ts, temp_c, humidity, battery, address)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    reading.ts.isoformat(),
                    reading.temp_c,
                    reading.humidity,
                    reading.battery,
                    reading.address,
                ),
            )

    def latest(self) -> Reading | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ts, temp_c, humidity, battery, address FROM sensor_readings"
                " ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return self._to_reading(row) if row else None

    def history(self, hours: int) -> list[Reading]:
        since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, temp_c, humidity, battery, address FROM sensor_readings"
                " WHERE ts >= ? ORDER BY ts",
                (since,),
            ).fetchall()
        return [self._to_reading(row) for row in rows]

    @staticmethod
    def _to_reading(row: tuple) -> Reading:
        ts, temp_c, humidity, battery, address = row
        return Reading(
            ts=datetime.fromisoformat(ts),
            temp_c=temp_c,
            humidity=humidity,
            battery=battery,
            address=address,
        )
