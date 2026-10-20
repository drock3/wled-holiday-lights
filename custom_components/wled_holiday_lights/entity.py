"""Base entity for WLED Holiday Lights."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .controller import LightingController


class HolidayLightingEntity(Entity):
    """Entity whose state lives in the controller and refreshes on its signal.

    Entity IDs come from the setup's name; the registry keeps existing IDs by unique ID.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: LightingController, key: str) -> None:
        self.controller = controller
        self._attr_unique_id = f"{controller.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, controller.entry.entry_id)},
            name=controller.name,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.controller.signal, self.async_write_ha_state)
        )
