"""Mode: Automatic, Manual (hands off for WLED editing) or Disabled (kept off)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HolidayLightingConfigEntry
from .const import MODE_MANUAL, MODES
from .entity import HolidayLightingEntity

ICONS = {
    "Automatic": "mdi:calendar-sync",
    "Manual": "mdi:hand-back-right",
    "Disabled": "mdi:power-off",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HolidayLightingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ModeSelect(entry.runtime_data, "mode")])


class ModeSelect(HolidayLightingEntity, SelectEntity):
    _attr_name = "Mode"
    _attr_options = MODES

    @property
    def current_option(self) -> str:
        return self.controller.mode

    @property
    def icon(self) -> str:
        return ICONS[self.controller.mode]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        returns = None
        if self.controller.mode == MODE_MANUAL and self.controller.manual_date:
            day = dt_util.parse_date(self.controller.manual_date)
            if day:
                returns = dt_util.start_of_local_day(day + timedelta(days=1)).isoformat()
        return {"returns_to_automatic": returns}

    async def async_select_option(self, option: str) -> None:
        self.controller.set_mode(option)
