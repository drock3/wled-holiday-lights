"""WLED Holiday Lights: WLED theme control driven by an ordered list of sources."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.service import async_extract_config_entry_ids
from homeassistant.helpers.storage import Store
from homeassistant.helpers.target import TargetSelection
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ID,
    CONF_SOURCES,
    DOMAIN,
    MODE_MANUAL,
    SERVICE_RECONCILE,
    STORAGE_VERSION,
)
from .controller import LightingController
from .sources import source_unique_id

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type HolidayLightingConfigEntry = ConfigEntry[LightingController]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def reconcile(call: ServiceCall) -> None:
        loaded = {
            entry.entry_id: entry for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        }
        if not loaded:
            raise ServiceValidationError("WLED Holiday Lights is not set up")
        if TargetSelection(call.data).has_any_target:
            entry_ids = await async_extract_config_entry_ids(call) & set(loaded)
            if not entry_ids:
                raise ServiceValidationError("The target isn't a WLED Holiday Lights setup")
        elif len(loaded) == 1:
            entry_ids = set(loaded)
        else:
            raise ServiceValidationError(
                "There is more than one lighting setup; choose one as the target"
            )
        controllers: list[LightingController] = [loaded[i].runtime_data for i in sorted(entry_ids)]
        if manual := [c.name for c in controllers if c.mode == MODE_MANUAL]:
            raise ServiceValidationError(
                f"Manual mode is on for {', '.join(manual)}; choose Automatic or Disabled first"
            )
        for controller in controllers:
            controller.request_reconcile("service")

    hass.services.async_register(DOMAIN, SERVICE_RECONCILE, reconcile)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after Configure changes settings or sources."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: HolidayLightingConfigEntry) -> bool:
    registry = er.async_get(hass)
    # Switches and diagnostics of removed sources.
    current = {
        source_unique_id(entry.entry_id, source[CONF_ID])
        for source in entry.options.get(CONF_SOURCES, [])
    }
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.domain in ("switch", "sensor")
            and entity.unique_id.startswith(source_unique_id(entry.entry_id, ""))
            and entity.unique_id not in current
        ):
            registry.async_remove(entity.entity_id)
    controller = LightingController(hass, entry)
    await controller.async_load()
    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    controller.async_start()
    entry.async_on_unload(controller.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HolidayLightingConfigEntry) -> bool:
    # Flush state first so a reload (or removal) sees the latest mode, brightness and switches.
    controller = entry.runtime_data
    await controller.async_shutdown()
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        for issue in (controller.issue_configuration, controller.issue_output):
            ir.async_delete_issue(hass, DOMAIN, issue)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
