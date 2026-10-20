"""Reconciliation: decide the plan and drive the device, one run at a time."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_CORE_CONFIG_UPDATE,
    SUN_EVENT_SUNRISE,
    SUN_EVENT_SUNSET,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from . import planner
from .const import (
    CALENDAR_REFRESH_INTERVAL,
    CONF_LIGHT,
    CONF_POWER,
    CONF_PRESET,
    CONF_SOURCES,
    CONF_WINDOWS,
    DEFAULT_BRIGHTNESS_PCT,
    DEFAULT_WINDOWS,
    MODE_AUTOMATIC,
    MODE_DISABLED,
    MODE_MANUAL,
    DOMAIN,
    ISSUE_CONFIGURATION,
    ISSUE_OUTPUT,
    RETRY_INTERVAL,
    STORAGE_VERSION,
)
from .device import LightingDevice
from .sources import CalendarSource, Source, create_source

_LOGGER = logging.getLogger(__name__)

NOT_READY = (STATE_UNAVAILABLE, STATE_UNKNOWN)


class LightingController:
    """Owns all lighting state and serialises reconciliation runs."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.name = entry.title
        # Settings live in data, and in options once changed; options also hold the sources.
        config = {**entry.data, **entry.options}
        self.device = LightingDevice(
            hass, self, config[CONF_LIGHT], config[CONF_PRESET], config.get(CONF_POWER) or None
        )
        # In priority order: the first source wins.
        self.sources: list[Source] = [
            create_source(hass, source) for source in config.get(CONF_SOURCES, [])
        ]
        self.windows: list[dict[str, Any]] = list(config.get(CONF_WINDOWS, DEFAULT_WINDOWS))
        self.signal = f"{DOMAIN}_{entry.entry_id}_update"
        self.issue_configuration = f"{ISSUE_CONFIGURATION}_{entry.entry_id}"
        self.issue_output = f"{ISSUE_OUTPUT}_{entry.entry_id}"
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )

        # Persisted state.
        self.mode = MODE_AUTOMATIC
        self.manual_date = ""  # Manual mode returns to Automatic after this local date
        self.brightness_pct = DEFAULT_BRIGHTNESS_PCT
        self.presets: list[str] = []
        self.output: dict[str, Any] = {"status": None, "detail": "", "look": "", "checked": None}
        self.source_enabled: dict[str, bool] = {source.id: True for source in self.sources}

        # Runtime state.
        self.plan: dict[str, Any] | None = None
        self.hours = False
        self.spans: list[dict[str, Any]] = []  # when each lighting window is open today
        self.next_hours_change: datetime | None = None
        self.themes: list[str] = []
        self._started = False
        self._task: asyncio.Task[None] | None = None
        self._unsubs: list[Callable[[], None]] = []
        self._boundary_unsub: Callable[[], None] | None = None
        self._hours_unsub: Callable[[], None] | None = None
        self._retry_unsub: Callable[[], None] | None = None
        # Survives interruption, so a forced re-apply isn't lost to a concurrent event.
        self._force_pending = False

    # ----- persistence and entity updates -------------------------------------------------

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.mode = data.get("mode", MODE_AUTOMATIC)
        self.manual_date = data.get("manual_date", "")
        self.brightness_pct = data.get("brightness_pct", DEFAULT_BRIGHTNESS_PCT)
        self.presets = data.get("presets", [])
        self.output.update(data.get("output", {}))
        # Flags of deleted sources are dropped.
        stored = data.get("source_enabled", {})
        self.source_enabled = {
            sid: stored.get(sid, True) is not False for sid in self.source_enabled
        }
        self.themes = planner.parse_library(self.presets)["themes"]

    def _snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "manual_date": self.manual_date,
            "brightness_pct": self.brightness_pct,
            "presets": self.presets,
            "output": self.output,
            "source_enabled": self.source_enabled,
        }

    def _save(self) -> None:
        self._store.async_delay_save(self._snapshot, 1)

    @callback
    def _updated(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    @property
    def brightness(self) -> int:
        """Target brightness on WLED's 1-255 scale (same rounding as light.turn_on brightness_pct)."""
        return max(1, round(self.brightness_pct * 255 / 100))

    @callback
    def set_output(self, status: str, detail: str, *, look: str = "") -> None:
        """Record what the hardware was verified to be doing."""
        self.output = {
            "status": status,
            "detail": detail,
            "look": look,
            "checked": dt_util.now().isoformat(),
        }
        if status != "error":
            self._cancel_retry()
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_output)
        self._save()
        self._updated()

    @callback
    def output_error(self, detail: str, *, message: str | None = None) -> None:
        self.set_output("error", detail)
        self._schedule_retry()
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.issue_output,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_OUTPUT,
            translation_placeholders={"name": self.name, "message": message or detail},
        )

    # ----- lifecycle ---------------------------------------------------------------------

    @callback
    def async_start(self) -> None:
        dev = self.device
        self._update_hours(trigger=False)
        self._unsubs += [
            async_track_time_interval(self.hass, self._calendar_refresh, CALENDAR_REFRESH_INTERVAL),
            async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=0),
            self.hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, self._location_changed),
            async_track_state_change_event(self.hass, dev.preset, self._preset_changed),
            async_track_state_change_event(self.hass, dev.light, self._light_changed),
            async_at_started(self.hass, self._ha_started),
        ]
        if dev.power:
            self._unsubs.append(
                async_track_state_change_event(self.hass, dev.power, self._power_changed)
            )
        for source in self.sources:
            source.async_start(self._source_changed)

    @callback
    def async_stop(self) -> None:
        self._started = False
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for source in self.sources:
            source.async_stop()
        if self._boundary_unsub:
            self._boundary_unsub()
            self._boundary_unsub = None
        if self._hours_unsub:
            self._hours_unsub()
            self._hours_unsub = None
        self._cancel_retry()
        if self._task:
            self._task.cancel()

    async def async_shutdown(self) -> None:
        """Stop, let any cancelled run finish, and write pending state before unload/reload."""
        self.async_stop()
        if self._task:
            await asyncio.wait([self._task])
        await self._store.async_save(self._snapshot())

    # ----- triggers ----------------------------------------------------------------------

    async def _ha_started(self, _hass: HomeAssistant) -> None:
        self._started = True
        self._update_hours(trigger=False)
        self._expire_manual()
        self.request_reconcile("start")

    @callback
    def _calendar_refresh(self, _now: datetime) -> None:
        self.request_reconcile("calendar refresh", interrupt=False, calendar_only=True)

    @callback
    def _cancel_retry(self) -> None:
        if self._retry_unsub:
            self._retry_unsub()
            self._retry_unsub = None

    @callback
    def _schedule_retry(self) -> None:
        if self._started and self.mode != MODE_MANUAL and self._retry_unsub is None:
            self._retry_unsub = async_track_point_in_time(
                self.hass, self._retry, dt_util.now() + RETRY_INTERVAL
            )

    @callback
    def _retry(self, _now: datetime) -> None:
        self._retry_unsub = None
        if self._task is not None and not self._task.done():
            self._schedule_retry()
        else:
            self.request_reconcile("retry", interrupt=False)

    @callback
    def _midnight(self, _now: datetime) -> None:
        self._update_hours(trigger=False)
        self._expire_manual()
        self.request_reconcile("midnight")

    @callback
    def _expire_manual(self) -> None:
        """Manual mode lasts until midnight, including a midnight missed while HA was offline."""
        if self.mode == MODE_MANUAL and self.manual_date != dt_util.now().date().isoformat():
            _LOGGER.info("Manual mode ended at midnight; returning to Automatic")
            self.mode = MODE_AUTOMATIC
            self.manual_date = ""
            self._save()
            self._updated()

    @callback
    def _hours_boundary(self, _now: datetime) -> None:
        self._hours_unsub = None
        self._update_hours()

    @callback
    def _location_changed(self, _event: Event) -> None:
        """Sunrise and sunset move with the home location."""
        self._update_hours(trigger=False)
        self.request_reconcile("location")

    @callback
    def _preset_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        old_options = old.attributes.get("options") if old else None
        new_options = new.attributes.get("options") if new else None
        old_available = old is not None and old.state != STATE_UNAVAILABLE
        new_available = new is not None and new.state != STATE_UNAVAILABLE
        self._refresh_preset_cache()
        changed_look = (
            new is not None
            and new.state in (new_options or [])
            and self.plan is not None
            and self.plan["allowed"]
            and new.state != self.plan["look"]
        )
        if old_options != new_options or old_available != new_available or changed_look:
            self.request_reconcile("presets", device_event=True)

    @callback
    def _light_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if self.plan is None:
            return
        allowed = self.plan["allowed"]
        recovered = (
            new is not None
            and (old is None or old.state in NOT_READY)
            and (new.state in (STATE_ON, STATE_OFF))
        )
        if allowed:
            drifted = (
                new is None
                or new.state != STATE_ON
                or new.attributes.get("brightness") != self.brightness
            )
        else:
            drifted = new is not None and new.state == STATE_ON
        if recovered or drifted:
            self.request_reconcile("light", device_event=True)

    @callback
    def _power_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if old is None or new is None or old.state != new.state:
            self.request_reconcile("outlet", device_event=True)

    @callback
    def _source_changed(self, name: str) -> None:
        self.request_reconcile(f"source {name}")

    @callback
    def _source_boundary(self, _now: datetime) -> None:
        self._boundary_unsub = None
        self.request_reconcile("source boundary")

    @callback
    def _update_hours(self, trigger: bool = True) -> None:
        now = dt_util.now()
        sun: dict[str, datetime | None] = {}
        for key, event in (
            (planner.SUNRISE, SUN_EVENT_SUNRISE),
            (planner.SUNSET, SUN_EVENT_SUNSET),
        ):
            moment = get_astral_event_date(self.hass, event, now.date())
            sun[key] = dt_util.as_local(moment) if moment else None
        spans = planner.window_spans(self.windows, now.date(), now.tzinfo, sun)
        change = planner.next_hours_change(now, spans)
        if self._hours_unsub:
            self._hours_unsub()
            self._hours_unsub = None
        if change is not None:
            self._hours_unsub = async_track_point_in_time(self.hass, self._hours_boundary, change)
        if spans != self.spans or change != self.next_hours_change:
            self.spans = spans
            self.next_hours_change = change
            self._updated()
        hours = planner.lighting_hours(now, spans)
        if hours != self.hours:
            self.hours = hours
            self._updated()
            if trigger:
                self.request_reconcile("lighting hours")

    @callback
    def _refresh_preset_cache(self) -> None:
        """Keep the last native preset list so planning works while WLED is powered down."""
        options = self.device.preset_options
        if options is not None and options != self.presets:
            self.presets = options
            self._save()
            self._updated()

    # ----- user actions ------------------------------------------------------------------

    @callback
    def set_mode(self, mode: str) -> None:
        """Automatic runs normally; Manual hands WLED over for editing; Disabled keeps it off."""
        if mode == self.mode:
            return
        self.mode = mode
        self.manual_date = dt_util.now().date().isoformat() if mode == MODE_MANUAL else ""
        self._save()
        self._updated()
        if mode == MODE_MANUAL:
            self._cancel_retry()
            self._force_pending = False
            self._start_task(self._enter_manual(), "manual mode")
        else:
            self.request_reconcile(f"mode {mode}")

    async def _enter_manual(self) -> None:
        """Power the outlet so WLED is reachable, then send nothing further."""
        await self.device.async_power_for_manual()

    @callback
    def set_source_enabled(self, source_id: str, enabled: bool) -> None:
        """Persist the flag without reloading the setup."""
        if self.source_enabled.get(source_id) == enabled:
            return
        self.source_enabled[source_id] = enabled
        self._save()
        self._updated()
        self.request_reconcile("source enabled" if enabled else "source disabled")

    @callback
    def set_brightness(self, percent: int) -> None:
        """Target level; applied at power-on and enforced at every reconciliation."""
        if percent == self.brightness_pct:
            return
        self.brightness_pct = percent
        self._save()
        self._updated()
        self.request_reconcile("brightness")

    # ----- reconciliation ----------------------------------------------------------------

    @callback
    def request_reconcile(
        self,
        reason: str,
        *,
        device_event: bool = False,
        interrupt: bool = True,
        calendar_only: bool = False,
        force: bool = False,
    ) -> None:
        """Start a run, cancelling any in-flight one (like an automation in restart mode).

        ``force`` resends every hardware command instead of only correcting drift.
        """
        # Manual mode: hands off the hardware entirely.
        if not self._started or self.mode == MODE_MANUAL:
            return
        running = self._task is not None and not self._task.done()
        # Startup/shutdown cause device changes of their own; don't restart their waits.
        if device_event and self.device.transitioning:
            return
        if running and not interrupt:
            return
        if not calendar_only:
            self._cancel_retry()
        if force:
            self._force_pending = True
        _LOGGER.debug("Reconcile requested: %s", reason)
        self._start_task(self._reconcile(calendar_only=calendar_only), f"reconcile ({reason})")

    @callback
    def _start_task(self, work: Coroutine[Any, Any, None], name: str) -> None:
        """Replace any in-flight device work, so only one run ever drives the hardware."""
        previous = self._task if self._task and not self._task.done() else None
        if previous:
            previous.cancel()
        self._task = self.entry.async_create_background_task(
            self.hass, self._run(previous, work), f"{DOMAIN} {name}"
        )

    async def _run(
        self, previous: asyncio.Task[None] | None, work: Coroutine[Any, Any, None]
    ) -> None:
        if previous:
            await asyncio.wait([previous])
        try:
            await work
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Lighting control failed")
            self.output_error("Lighting control failed; see the Home Assistant log.")

    def plan_is_current(self, plan: dict[str, Any]) -> bool:
        """Guard against activating a plan made stale by time passing during startup."""
        return (
            self.plan is plan
            and self.mode == (MODE_AUTOMATIC if plan["enabled"] else MODE_DISABLED)
            and self.hours
            and dt_util.now().date().isoformat() == plan["date"]
        )

    async def _reconcile(self, *, calendar_only: bool = False) -> None:
        dev = self.device
        now = dt_util.now()
        if calendar_only:
            calendars = [
                source
                for source in self.sources
                if isinstance(source, CalendarSource) and self.source_enabled[source.id]
            ]
            changed = [await source.async_refresh(now) for source in calendars]
            if not any(changed):
                return
            self._cancel_retry()
        self._update_hours(trigger=False)

        enabled = self.mode == MODE_AUTOMATIC

        # Discover presets once after a fresh install; afterwards the cache avoids idle boots.
        if enabled and self.hours and not self.presets and not dev.preset_options:
            await dev.async_power_on()
        self._refresh_preset_cache()

        live = dev.preset_options
        library = planner.parse_library(live if live is not None else self.presets)
        self.themes = library["themes"]
        self._updated()

        # Enforce the gate before any potentially slow calendar query.
        if not enabled or not self.hours:
            await dev.async_power_off(
                "Automation disabled" if not enabled else "Outside lighting hours"
            )

        now = dt_util.now()
        results = [
            await source.async_evaluate(now, refresh=not calendar_only)
            if self.source_enabled[source.id]
            else {
                "id": source.id,
                "name": source.name,
                "type": source.type,
                "enabled": False,
                "requests": [],
                "available": True,
                "warnings": [],
            }
            for source in self.sources
        ]
        power_ok = dev.power is None or dev.state(dev.power) in (STATE_ON, STATE_OFF)
        plan = planner.build_plan(
            now=now,
            library=library,
            enabled=enabled,
            sources=results,
            hours=self.hours,
            power_ok=power_ok,
            presets_loaded=live is not None or bool(self.presets),
        )
        self.plan = plan
        self._updated()
        self._schedule_boundary()

        if plan["errors"]:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.issue_configuration,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_CONFIGURATION,
                translation_placeholders={
                    "name": self.name,
                    "errors": "\n".join(f"- {e}" for e in plan["errors"]),
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_configuration)

        await dev.async_apply(plan, force=self._force_pending)
        self._force_pending = False
        if not power_ok or any(r["enabled"] and not r["available"] for r in results):
            self._schedule_retry()

    @callback
    def _schedule_boundary(self) -> None:
        """Wake exactly when a source's result next changes, such as a calendar event boundary."""
        if self._boundary_unsub:
            self._boundary_unsub()
            self._boundary_unsub = None
        boundary = min(
            (
                moment
                for source in self.sources
                if self.source_enabled[source.id] and (moment := source.next_change())
            ),
            default=None,
        )
        if boundary:
            self._boundary_unsub = async_track_point_in_time(
                self.hass, self._source_boundary, boundary
            )
