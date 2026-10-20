"""Button that resends the current plan to the WLED hardware."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolidayLightingConfigEntry
from .const import MODE_MANUAL
from .entity import HolidayLightingEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HolidayLightingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ReapplyButton(entry.runtime_data, "reapply")])


class ReapplyButton(HolidayLightingEntity, ButtonEntity):
    """Re-evaluate the plan and resend every power, preset and brightness command."""

    _attr_name = "Reapply to WLED"
    _attr_icon = "mdi:lightbulb-auto"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        if self.controller.mode == MODE_MANUAL:
            raise HomeAssistantError("Manual mode is on; choose Automatic or Disabled first")
        self.controller.request_reconcile("reapply button", force=True)
