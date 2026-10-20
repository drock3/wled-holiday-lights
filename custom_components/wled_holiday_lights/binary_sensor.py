"""Lighting hours and diagnostic binary sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolidayLightingConfigEntry
from .const import MODE_AUTOMATIC
from .controller import LightingController
from .entity import HolidayLightingEntity


def _plan_flag(key: str) -> Callable[[LightingController], bool | None]:
    return lambda ctl: bool(ctl.plan[key]) if ctl.plan else None


def _hours_attrs(ctl: LightingController) -> dict[str, Any]:
    return {
        "windows": [
            {
                "name": span["name"],
                "start": span["start"].isoformat() if span["start"] else None,
                "end": span["end"].isoformat() if span["end"] else None,
            }
            for span in ctl.spans
        ],
        "next_change": ctl.next_hours_change.isoformat() if ctl.next_hours_change else None,
    }


@dataclass(frozen=True, kw_only=True)
class LightingBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[LightingController], bool | None]
    attrs_fn: Callable[[LightingController], dict[str, Any] | None] | None = None


BINARY_SENSORS: tuple[LightingBinarySensorDescription, ...] = (
    LightingBinarySensorDescription(
        key="hours",
        name="In lighting hours",
        icon="mdi:clock-outline",
        value_fn=lambda ctl: ctl.hours,
        attrs_fn=_hours_attrs,
    ),
    LightingBinarySensorDescription(
        key="allowed",
        name="Ready to light",
        icon="mdi:power-plug-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctl: (
            ctl.mode == MODE_AUTOMATIC
            and ctl.hours
            and (bool(ctl.plan["allowed"]) if ctl.plan else None)
        ),
    ),
    LightingBinarySensorDescription(
        key="configuration_problem",
        name="Setup problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_plan_flag("errors"),
        attrs_fn=lambda ctl: {"errors": ctl.plan["errors"] if ctl.plan else []},
    ),
    LightingBinarySensorDescription(
        key="output_problem",
        name="Output control problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctl: ctl.output["status"] == "error" if ctl.output.get("status") else None,
        attrs_fn=lambda ctl: {"detail": ctl.output.get("detail")},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HolidayLightingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(LightingBinarySensor(entry.runtime_data, d) for d in BINARY_SENSORS)


class LightingBinarySensor(HolidayLightingEntity, BinarySensorEntity):
    entity_description: LightingBinarySensorDescription
    _unrecorded_attributes = frozenset({"errors"})

    def __init__(
        self, controller: LightingController, description: LightingBinarySensorDescription
    ) -> None:
        super().__init__(controller, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.controller)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(self.controller) if attrs_fn else None
