"""WLED output control: optional outlet power, readiness, preset activation and verification."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
import logging
import time as monotonic_time
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONFIRM_TIMEOUT,
    RELOAD_INTERVAL,
    SERVICE_TIMEOUT,
    STARTUP_POLL,
    STARTUP_TIMEOUT,
)

if TYPE_CHECKING:
    from .controller import LightingController

_LOGGER = logging.getLogger(__name__)


class LightingDevice:
    """One WLED controller, optionally behind a switched outlet."""

    def __init__(
        self,
        hass: HomeAssistant,
        controller: LightingController,
        light: str,
        preset: str,
        power: str | None,
    ) -> None:
        self.hass = hass
        self.controller = controller
        self.light = self._resolve_light(light)
        self.preset = preset
        self.power = power
        self._transitions = 0
        self._reload_task: asyncio.Task[None] | None = None

    def _resolve_light(self, light: str) -> str:
        """Use the native master light when a WLED segment was selected."""
        registry = er.async_get(self.hass)
        selected = registry.async_get(light)
        if selected is None or selected.platform != "wled":
            return light
        # Native WLED uses the MAC for the master and MAC_segment for segments.
        master_id, separator, segment = selected.unique_id.rpartition("_")
        if not separator or not segment.isdecimal():
            return light
        entity_id = registry.async_get_entity_id("light", "wled", master_id)
        master = registry.async_get(entity_id) if entity_id else None
        if (
            master is not None
            and master.platform == "wled"
            and master.config_entry_id == selected.config_entry_id
            and master.device_id == selected.device_id
        ):
            _LOGGER.info(
                "Using WLED master light %s instead of segment %s", master.entity_id, light
            )
            return master.entity_id
        return light

    @property
    def transitioning(self) -> bool:
        """True while powering on/off; device events then must not restart reconciliation."""
        return self._transitions > 0

    @asynccontextmanager
    async def _transition(self) -> AsyncIterator[None]:
        self._transitions += 1
        try:
            yield
        finally:
            self._transitions -= 1

    def state(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        return state.state if state else STATE_UNKNOWN

    def attr(self, entity_id: str, attribute: str) -> Any:
        state = self.hass.states.get(entity_id)
        return state.attributes.get(attribute) if state else None

    @property
    def preset_available(self) -> bool:
        return self.state(self.preset) != STATE_UNAVAILABLE

    @property
    def preset_options(self) -> list[str] | None:
        """Live preset options, or None when the preset entity can't provide them."""
        options = self.attr(self.preset, "options")
        return list(options) if options is not None and self.preset_available else None

    @property
    def powered(self) -> bool:
        """The outlet is on, or there is no outlet."""
        return self.power is None or self.state(self.power) == STATE_ON

    @property
    def entities(self) -> list[str]:
        return [e for e in (self.power, self.light, self.preset) if e]

    def ready(self) -> bool:
        return (
            self.powered
            and self.state(self.light) in (STATE_ON, STATE_OFF)
            and self.preset_available
            and len(self.attr(self.preset, "options") or []) > 0
        )

    async def _call(
        self, domain: str, service: str, data: dict[str, Any], timeout: float = SERVICE_TIMEOUT
    ) -> bool:
        """Call a service, logging rather than raising on failure (continue_on_error)."""
        try:
            async with asyncio.timeout(timeout):
                await self.hass.services.async_call(domain, service, data, blocking=True)
        except Exception as err:  # noqa: BLE001 - any failure is verified via state afterwards
            _LOGGER.warning("%s.%s %s failed: %s", domain, service, data, err or type(err).__name__)
            return False
        return True

    async def _wait(
        self, predicate: Callable[[], bool], entity_ids: Iterable[str], timeout: float
    ) -> bool:
        """Wait until predicate holds, re-checking on state changes of entity_ids."""
        if predicate():
            return True
        if timeout <= 0:
            return False
        done = asyncio.Event()

        @callback
        def _changed(_event: Event[EventStateChangedData]) -> None:
            if predicate():
                done.set()

        unsub = async_track_state_change_event(self.hass, list(entity_ids), _changed)
        try:
            async with asyncio.timeout(timeout):
                await done.wait()
        except TimeoutError:
            return predicate()
        finally:
            unsub()
        return True

    async def _reload(self, entry_id: str) -> None:
        try:
            await self.hass.config_entries.async_reload(entry_id)
        except Exception as err:  # noqa: BLE001 - startup keeps waiting and reports a timeout
            _LOGGER.warning("Reloading config entry %s failed: %s", entry_id, err)

    async def async_power_off(self, reason: str, *, force: bool = False) -> bool:
        """Gracefully turn off the LEDs, then cut and verify outlet power (or the light)."""
        async with self._transition():
            light = self.state(self.light)
            if light == STATE_ON or (force and light == STATE_OFF):
                await self._call("light", "turn_off", {"entity_id": self.light})
            if self.power is None:
                if not await self._wait(
                    lambda: self.state(self.light) == STATE_OFF, [self.light], CONFIRM_TIMEOUT
                ):
                    self.controller.output_error(
                        "WLED did not confirm off.",
                        message="WLED did not confirm off. Check WLED immediately.",
                    )
                    return False
                self.controller.set_output("off", reason)
                return True
            if force or self.state(self.power) != STATE_OFF:
                await self._call("switch", "turn_off", {"entity_id": self.power})
            if not await self._wait(
                lambda: self.state(self.power) == STATE_OFF, [self.power], CONFIRM_TIMEOUT
            ):
                self.controller.output_error(
                    "WLED outlet did not confirm power off.",
                    message="WLED outlet did not confirm power off. Check the outlet immediately.",
                )
                return False
            self.controller.set_output("off", reason)
            return True

    async def async_power_for_manual(self) -> None:
        """Manual mode: switch the outlet on so WLED can be edited; nothing else."""
        if self.power is None:
            self.controller.set_output(
                "manual", "Manual mode: automatic output paused until midnight."
            )
            return
        async with self._transition():
            if self.state(self.power) != STATE_ON:
                await self._call("switch", "turn_on", {"entity_id": self.power})
            if await self._wait(
                lambda: self.state(self.power) == STATE_ON, [self.power], CONFIRM_TIMEOUT
            ):
                self.controller.set_output(
                    "manual", "Manual mode: WLED outlet on; automatic output paused until midnight."
                )
            else:
                self.controller.output_error("WLED outlet did not turn on for manual mode.")

    async def async_power_on(self, *, force: bool = False) -> bool:
        """Power the outlet, if any, and wait up to two minutes for WLED and its presets."""
        async with self._transition():
            if self.power and (force or self.state(self.power) != STATE_ON):
                await self._call("switch", "turn_on", {"entity_id": self.power})
            start = monotonic_time.monotonic()
            deadline = start + STARTUP_TIMEOUT
            reload_at = start + RELOAD_INTERVAL
            while True:
                await self._call(
                    "homeassistant",
                    "update_entity",
                    {"entity_id": self.light},
                    timeout=CONFIRM_TIMEOUT,
                )
                remaining = deadline - monotonic_time.monotonic()
                if await self._wait(
                    self.ready, self.entities, min(STARTUP_POLL, max(0, remaining))
                ):
                    return True
                now = monotonic_time.monotonic()
                # If HA started while the outlet was off, the WLED entry may never load
                # on its own; reload it once the device has had time to boot.
                if (
                    now >= reload_at
                    and self.state(self.light) in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                    and (self._reload_task is None or self._reload_task.done())
                ):
                    entry = er.async_get(self.hass).async_get(self.light)
                    if entry and entry.config_entry_id:
                        _LOGGER.info("Reloading %s's config entry after startup delay", self.light)
                        # Runs independently so cancelling reconciliation can't interrupt it.
                        self._reload_task = self.hass.async_create_background_task(
                            self._reload(entry.config_entry_id), f"reload {entry.config_entry_id}"
                        )
                        reload_at = now + RELOAD_INTERVAL
                if now >= deadline:
                    break
            if self.power is None:
                if self.state(self.light) == STATE_ON:
                    await self._call("light", "turn_off", {"entity_id": self.light})
                self.controller.output_error("WLED did not become ready within 120 seconds.")
                return False
            if await self.async_power_off("WLED startup failed."):
                self.controller.output_error(
                    "WLED did not become ready within 120 seconds; outlet powered off."
                )
            return False

    async def async_apply(self, plan: dict[str, Any], *, force: bool = False) -> None:
        """Make the hardware match the plan, verifying each step and failing off.

        ``force`` resends every command even when the hardware already reports the target.
        """
        async with self._transition():
            await self._async_apply(plan, force)

    async def _async_apply(self, plan: dict[str, Any], force: bool) -> None:
        ctl = self.controller
        was_on = not force and self.powered and self.state(self.light) == STATE_ON
        brightness = ctl.brightness
        # WLED clears its preset ID on any power or brightness change, so remember the applied look.
        applied_look = ctl.output.get("look") or "" if ctl.output.get("status") == "applied" else ""
        look = plan["look"]

        if not (plan["allowed"] and ctl.plan_is_current(plan)):
            await self.async_power_off(off_reason(plan), force=force)
            return

        if not await self.async_power_on(force=force):
            return

        was_on = was_on and self.state(self.light) == STATE_ON
        options = self.preset_options or []
        if look not in options:
            if await self.async_power_off("Intended preset is missing or unavailable."):
                ctl.output_error("Intended preset is missing or unavailable; activation refused.")
            return

        # Startup can take two minutes; check the window again right before activation.
        if not ctl.plan_is_current(plan):
            await self.async_power_off(
                "Activation cancelled because the plan or lighting window changed."
            )
            return

        if not was_on:
            await self._call(
                "light", "turn_on", {"entity_id": self.light, "brightness": brightness}
            )

        # Power-on clears WLED's preset ID, so select after powering on. Skip when this look
        # is already applied and no other preset has been chosen, so the animation keeps running.
        current_preset = self.state(self.preset)
        if (
            not was_on
            or applied_look != look
            or (current_preset in options and current_preset != look)
        ):
            await self._call("select", "select_option", {"entity_id": self.preset, "option": look})
            if not await self._wait(
                lambda: self.state(self.preset) == look, [self.preset], CONFIRM_TIMEOUT
            ):
                if await self.async_power_off("Preset selection failed."):
                    ctl.output_error("Preset selection failed; power-off requested.")
                return

        # Presets can save power off even with the target brightness.
        if self.state(self.light) != STATE_ON or self.attr(self.light, "brightness") != brightness:
            await self._call(
                "light", "turn_on", {"entity_id": self.light, "brightness": brightness}
            )

        def verified() -> bool:
            return (
                self.powered
                and self.state(self.light) == STATE_ON
                and self.attr(self.light, "brightness") == brightness
            )

        if not await self._wait(
            verified, [e for e in (self.power, self.light) if e], CONFIRM_TIMEOUT
        ):
            if await self.async_power_off("Output verification failed."):
                ctl.output_error("WLED did not confirm intended power and brightness.")
            return

        ctl.set_output("applied", look, look=look)


def off_reason(plan: dict[str, Any]) -> str:
    """Why the output is off; the plan's reason alone may name a theme that is blocked."""
    if not plan["enabled"]:
        return "Automation disabled"
    if not plan["hours"]:
        return "Outside lighting hours"
    if plan["errors"]:
        return "Setup problem; see Repairs"
    return plan["reason"]
