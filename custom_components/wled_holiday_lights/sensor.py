"""Plan, output and diagnostic sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HolidayLightingConfigEntry
from .controller import LightingController
from .entity import HolidayLightingEntity
from .sources import Source


def _plan(ctl: LightingController, key: str) -> Any:
    return ctl.plan.get(key) if ctl.plan else None


def _count(ctl: LightingController, key: str) -> int | None:
    return len(ctl.plan[key]) if ctl.plan else None


def _timestamp(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None


@dataclass(frozen=True, kw_only=True)
class LightingSensorDescription(SensorEntityDescription):
    value_fn: Callable[[LightingController], Any]
    attrs_fn: Callable[[LightingController], dict[str, Any] | None] | None = None


SENSORS: tuple[LightingSensorDescription, ...] = (
    LightingSensorDescription(
        key="plan",
        name="Scheduled theme",
        icon="mdi:string-lights",
        value_fn=lambda ctl: _plan(ctl, "theme"),
        # Full plan for dashboards.
        attrs_fn=lambda ctl: (
            {k: v for k, v in ctl.plan.items() if k != "theme"} if ctl.plan else None
        ),
    ),
    LightingSensorDescription(
        key="look",
        name="Scheduled preset",
        icon="mdi:palette-swatch-variant",
        value_fn=lambda ctl: _plan(ctl, "look") or ("None" if ctl.plan else None),
    ),
    LightingSensorDescription(
        key="reason",
        name="Schedule status",
        icon="mdi:information-outline",
        value_fn=lambda ctl: _plan(ctl, "reason"),
    ),
    LightingSensorDescription(
        key="output",
        name="Last output result",
        translation_key="output",
        icon="mdi:led-strip-variant",
        device_class=SensorDeviceClass.ENUM,
        options=["applied", "off", "manual", "error"],
        value_fn=lambda ctl: ctl.output.get("status"),
        attrs_fn=lambda ctl: {k: ctl.output.get(k) for k in ("detail", "look", "checked")},
    ),
    LightingSensorDescription(
        key="output_detail",
        name="Output result detail",
        icon="mdi:message-text-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctl: ctl.output.get("detail") or None,
    ),
    LightingSensorDescription(
        key="last_output_check",
        name="Output result recorded",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctl: _timestamp(ctl.output.get("checked")),
    ),
    LightingSensorDescription(
        key="last_evaluated",
        name="Schedule last evaluated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctl: _timestamp(_plan(ctl, "evaluated")),
    ),
    LightingSensorDescription(
        key="source",
        name="Winning source",
        icon="mdi:format-list-numbered",
        value_fn=lambda ctl: _plan(ctl, "source") or ("None" if ctl.plan else None),
    ),
    LightingSensorDescription(
        key="active_requests",
        name="Active requests",
        icon="mdi:format-list-checks",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctl: _count(ctl, "requests"),
        attrs_fn=lambda ctl: {
            "requests": [
                f"{r['source']}: {r['theme']} ({r['status']})" for r in _plan(ctl, "requests") or []
            ]
        },
    ),
    LightingSensorDescription(
        key="warnings",
        name="Warnings",
        icon="mdi:alert-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctl: _count(ctl, "warnings"),
        attrs_fn=lambda ctl: {"warnings": _plan(ctl, "warnings") or []},
    ),
    LightingSensorDescription(
        key="themes",
        name="Known themes",
        icon="mdi:shape-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctl: len(ctl.themes),
        attrs_fn=lambda ctl: {"themes": ctl.themes},
    ),
    LightingSensorDescription(
        key="presets",
        name="Known WLED presets",
        icon="mdi:playlist-star",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda ctl: len(ctl.presets),
        attrs_fn=lambda ctl: {"options": ctl.presets},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HolidayLightingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    async_add_entities(LightingSensor(controller, d) for d in SENSORS)
    async_add_entities(SourceThemeSensor(controller, source) for source in controller.sources)


class SourceThemeSensor(HolidayLightingEntity, SensorEntity):
    """The theme a source would drive independently of other sources' priority."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:string-lights"
    _unrecorded_attributes = frozenset({"requests", "warnings"})

    def __init__(self, controller: LightingController, source: Source) -> None:
        super().__init__(controller, f"source_{source.id}")
        self.source = source
        self._attr_name = f"{source.name} theme"

    @property
    def _status(self) -> dict[str, Any] | None:
        return next(
            (
                s
                for s in (self.controller.plan or {}).get("sources", [])
                if s["id"] == self.source.id
            ),
            None,
        )

    @property
    def native_value(self) -> str | None:
        status = self._status
        return (status["theme"] or status["reason"]) if status else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        status = self._status
        if status is None:
            return None
        requests = [
            r
            for r in (self.controller.plan or {}).get("requests", [])
            if r["source_id"] == self.source.id
        ]
        return {
            "type": status["type"],
            "position": status["position"],
            "enabled": status["enabled"],
            "available": status["available"],
            "theme": status["theme"],
            "look": status["look"],
            "detail": status["reason"],
            "winning": any(r["status"] == "won" for r in requests),
            "active_requests": status["requests"],
            "requests": requests,
            "warnings": status["warnings"],
        }


class LightingSensor(HolidayLightingEntity, SensorEntity):
    entity_description: LightingSensorDescription
    # Keep bulky lists and timestamps out of the recorder.
    _unrecorded_attributes = frozenset(
        {"requests", "sources", "warnings", "errors", "themes", "evaluated", "checked", "options"}
    )

    def __init__(
        self, controller: LightingController, description: LightingSensorDescription
    ) -> None:
        super().__init__(controller, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.controller)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(self.controller) if attrs_fn else None
