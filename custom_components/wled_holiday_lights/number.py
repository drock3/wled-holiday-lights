"""Number controls: brightness."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolidayLightingConfigEntry
from .entity import HolidayLightingEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HolidayLightingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([BrightnessNumber(entry.runtime_data, "brightness")])


class BrightnessNumber(HolidayLightingEntity, NumberEntity):
    """Changes made directly on WLED are reverted to this at the next reconciliation."""

    _attr_name = "Brightness"
    _attr_icon = "mdi:brightness-6"
    _attr_native_min_value = 1
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> int:
        return self.controller.brightness_pct

    async def async_set_native_value(self, value: float) -> None:
        self.controller.set_brightness(round(value))
