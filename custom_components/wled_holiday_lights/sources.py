"""Lighting sources: each turns one input into zero or more theme requests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.event import (
    TrackTemplate,
    TrackTemplateResult,
    async_track_state_change_event,
    async_track_template_result,
)
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from . import planner
from .const import (
    CALENDAR_TIMEOUT,
    CONF_ENTITY,
    CONF_ID,
    CONF_NAME,
    CONF_TEMPLATE,
    CONF_THEME,
    CONF_TYPE,
    SOURCE_CALENDAR,
    SOURCE_CONDITION,
    SOURCE_TEMPLATE,
)

_LOGGER = logging.getLogger(__name__)

NOT_READY = (STATE_UNAVAILABLE, STATE_UNKNOWN)
# Template results that mean "no theme".
EMPTY_RESULTS = ("", "none", "false", "unknown", "unavailable")

type RequestReconcile = Callable[[str], None]


class Source:
    """Base class; subclasses implement ``async_evaluate``."""

    type: str

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        self.hass = hass
        self.id: str = config[CONF_ID]
        self.name: str = config[CONF_NAME]
        self._unsubs: list[Callable[[], None]] = []

    def watched_entities(self) -> list[str]:
        """Entities whose state changes should trigger re-evaluation."""
        return []

    async def async_evaluate(self, now: datetime, *, refresh: bool = True) -> dict[str, Any]:
        raise NotImplementedError

    def next_change(self) -> datetime | None:
        """When this source's result next changes on its own, if known exactly."""
        return None

    @callback
    def async_start(self, request_reconcile: RequestReconcile) -> None:
        for entity_id in self.watched_entities():
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, entity_id, self._state_listener(request_reconcile)
                )
            )

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    def _state_listener(
        self, request_reconcile: RequestReconcile
    ) -> Callable[[Event[EventStateChangedData]], None]:
        @callback
        def changed(_event: Event[EventStateChangedData]) -> None:
            request_reconcile(self.name)

        return changed

    def _result(
        self,
        requests: list[dict[str, Any]] | None = None,
        *,
        available: bool = True,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "enabled": True,
            "requests": requests or [],
            "available": available,
            "warnings": warnings or [],
        }


class CalendarSource(Source):
    """Requests the title of each calendar event active now, shortest first."""

    type = SOURCE_CALENDAR

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        super().__init__(hass, config)
        self.calendar: str = config[CONF_ENTITY]
        # Last query: (local date, events or None when it failed).
        self.snapshot: tuple[str, list[dict[str, Any]] | None] | None = None
        self._next: datetime | None = None

    def watched_entities(self) -> list[str]:
        return [self.calendar]

    def next_change(self) -> datetime | None:
        return self._next

    async def async_refresh(self, now: datetime) -> bool:
        """Query today's events; True if they differ from the previous query."""
        snapshot = (now.date().isoformat(), await self._events(now))
        changed = snapshot != self.snapshot
        self.snapshot = snapshot
        return changed

    async def async_evaluate(self, now: datetime, *, refresh: bool = True) -> dict[str, Any]:
        if refresh or self.snapshot is None:
            await self.async_refresh(now)
        assert self.snapshot is not None
        events = self.snapshot[1]
        self._next = planner.next_boundary(events, now)
        if events is None:
            return self._result(
                available=False,
                warnings=[f"Calendar {self.calendar} unavailable or query failed."],
            )
        requests, warnings = planner.calendar_requests(events, now)
        return self._result(requests, warnings=warnings)

    async def _events(self, now: datetime) -> list[dict[str, Any]] | None:
        """Today's events, or None when the calendar can't be read."""
        state = self.hass.states.get(self.calendar)
        if state is None or state.state in NOT_READY:
            return None
        start = dt_util.start_of_local_day(now)
        try:
            async with asyncio.timeout(CALENDAR_TIMEOUT):
                response = await self.hass.services.async_call(
                    "calendar",
                    "get_events",
                    {
                        "entity_id": self.calendar,
                        "start_date_time": start.isoformat(),
                        "end_date_time": (start + timedelta(days=1)).isoformat(),
                    },
                    blocking=True,
                    return_response=True,
                )
        except Exception as err:  # noqa: BLE001 - reported as a source warning
            _LOGGER.warning("%s: calendar query failed: %s", self.name, err or type(err).__name__)
            return None
        result = (response or {}).get(self.calendar)
        if not isinstance(result, dict):
            return None
        return [dict(event) for event in result.get("events", [])]


class ConditionSource(Source):
    """Requests its theme while an entity's state is ``on``."""

    type = SOURCE_CONDITION

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        super().__init__(hass, config)
        self.entity: str = config[CONF_ENTITY]
        self.theme: str = config[CONF_THEME].strip()

    def watched_entities(self) -> list[str]:
        return [self.entity]

    def _state_listener(
        self, request_reconcile: RequestReconcile
    ) -> Callable[[Event[EventStateChangedData]], None]:
        @callback
        def changed(event: Event[EventStateChangedData]) -> None:
            old, new = event.data["old_state"], event.data["new_state"]
            # Attribute-only updates can't change the result.
            if old is None or new is None or old.state != new.state:
                request_reconcile(self.name)

        return changed

    async def async_evaluate(self, now: datetime, *, refresh: bool = True) -> dict[str, Any]:
        state = self.hass.states.get(self.entity)
        if state is None:
            return self._result(available=False, warnings=[f"{self.entity} not found."])
        if state.state in NOT_READY:
            return self._result(available=False, warnings=[f"{self.entity} is {state.state}."])
        if state.state == STATE_ON:
            return self._result(
                [
                    {
                        "theme": self.theme,
                        "detail": f"{self.entity} is on",
                        "start": None,
                        "end": None,
                    }
                ]
            )
        return self._result()


class TemplateSource(Source):
    """Requests the theme a Home Assistant template renders, if any."""

    type = SOURCE_TEMPLATE

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        super().__init__(hass, config)
        self.template = Template(config[CONF_TEMPLATE], hass)
        self._last: Any = None

    @callback
    def async_start(self, request_reconcile: RequestReconcile) -> None:
        @callback
        def rendered(_event: Event | None, updates: list[TrackTemplateResult]) -> None:
            result = updates[-1].result
            key = str(result) if isinstance(result, TemplateError) else _theme(result)
            if key != self._last:
                self._last = key
                request_reconcile(self.name)

        # Re-renders when referenced entities change, and every minute when it uses now().
        info = async_track_template_result(
            self.hass, [TrackTemplate(self.template, None)], rendered
        )
        self._unsubs.append(info.async_remove)

    async def async_evaluate(self, now: datetime, *, refresh: bool = True) -> dict[str, Any]:
        try:
            result = self.template.async_render(parse_result=False)
        except TemplateError as err:
            return self._result(available=False, warnings=[f"Template error: {err}"])
        theme = _theme(result)
        if not theme:
            return self._result()
        return self._result(
            [{"theme": theme, "detail": "Template result", "start": None, "end": None}]
        )


def _theme(result: Any) -> str:
    text = str(result if result is not None else "").strip()
    return "" if text.lower() in EMPTY_RESULTS else text


SOURCE_CLASSES: dict[str, type[Source]] = {
    SOURCE_CALENDAR: CalendarSource,
    SOURCE_CONDITION: ConditionSource,
    SOURCE_TEMPLATE: TemplateSource,
}


def source_unique_id(entry_id: str, source_id: str) -> str:
    """Unique ID of a source's Enabled switch and theme diagnostic sensor."""
    return f"{entry_id}_source_{source_id}"


def create_source(hass: HomeAssistant, config: dict[str, Any]) -> Source:
    return SOURCE_CLASSES[config[CONF_TYPE]](hass, config)
