from __future__ import annotations

from inky_dash.modules.base import Module
from inky_dash.modules.calendar import CalendarModule
from inky_dash.modules.clock import ClockModule
from inky_dash.modules.economist import EconomistModule
from inky_dash.modules.govee import GoveeModule
from inky_dash.modules.moon import MoonModule
from inky_dash.modules.photos import PhotosModule
from inky_dash.modules.vox import VoxModule
from inky_dash.modules.weather import WeatherModule
from inky_dash.modules.xkcd import XkcdModule

MODULES: dict[str, Module] = {
    module.id: module
    for module in [
        ClockModule(),
        WeatherModule(),
        GoveeModule(),
        EconomistModule(),
        CalendarModule(),
        XkcdModule(),
        VoxModule(),
        MoonModule(),
        PhotosModule(),
    ]
}
