"""One Enabled switch per lighting source."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolidayLightingConfigEntry
from .controller import LightingController
from .entity import HolidayLightingEntity
from .sources import Source

ICONS = {
    "calendar": "mdi:calendar",
    "condition": "mdi:toggle-switch-outline",
    "template": "mdi:code-braces",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HolidayLightingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    async_add_entities(SourceSwitch(controller, source) for source in controller.sources)


class SourceSwitch(HolidayLightingEntity, SwitchEntity):
    """Turns a source off without deleting it; a disabled source isn't evaluated."""

    _unrecorded_attributes = frozenset({"warnings"})

    def __init__(self, controller: LightingController, source: Source) -> None:
        super().__init__(controller, f"source_{source.id}")
        self.source = source
        self._attr_name = source.name
        self._attr_icon = ICONS.get(source.type)

    @property
    def is_on(self) -> bool:
        return self.controller.source_enabled.get(self.source.id, True)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        position = next(
            (i for i, s in enumerate(self.controller.sources, start=1) if s.id == self.source.id),
            None,
        )
        status = next(
            (
                s
                for s in (self.controller.plan or {}).get("sources", [])
                if s["id"] == self.source.id
            ),
            None,
        )
        return {
            "type": self.source.type,
            "position": position,
            "available": status["available"] if status else None,
            "active_requests": status["requests"] if status else 0,
            "warnings": status["warnings"] if status else [],
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.controller.set_source_enabled(self.source.id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.controller.set_source_enabled(self.source.id, False)
