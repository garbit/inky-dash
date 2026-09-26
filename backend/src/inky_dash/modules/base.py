from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from PIL import Image

from inky_dash.config import AppConfig
from inky_dash.layout import Rect


@runtime_checkable
class Module(Protocol):
    """One dashboard panel: a data fetcher plus a renderer for its layout slot,
    with a declarative options schema the config UI renders (see
    modules/options.py — the frontend has no per-module code)."""

    id: str
    name: str  # display name shown in the config UI
    ttl: int  # seconds fetch results stay fresh
    config_key: str  # AppConfig section this module's options edit
    fetch_key: str | None  # key in FetchConfig, or None if nothing to fetch
    options: list[dict]  # option items (empty = nothing user-configurable)

    async def fetch(self, config: AppConfig) -> dict[str, Any]: ...

    def render(
        self, image: Image.Image, rect: Rect, data: dict[str, Any] | None, config: AppConfig
    ) -> None: ...
