"""Compatibility tests using real Home Assistant flows, registries and services."""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.wled_holiday_lights.const import DOMAIN, MODE_MANUAL
from custom_components.wled_holiday_lights.controller import LightingController

EVENING = {
    "name": "Evening",
    "start": "sunset",
    "start_offset": 0,
    "end": "time",
    "end_time": "00:00:00",
}


def config_entry(name: str = "Front lights", suffix: str = "") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=name,
        data={"light": f"light.wled{suffix}", "preset": f"select.wled{suffix}", "power": None},
        options={"sources": [], "windows": [EVENING]},
        version=1,
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    with patch.object(LightingController, "async_start"):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"name": " Front lights ", "light": "light.wled", "preset": "select.wled"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Front lights"
    assert result["data"] == {"light": "light.wled", "preset": "select.wled", "power": None}
    assert result["options"] == {"sources": [], "windows": [EVENING]}
    assert result["result"].state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(result["result"].entry_id)


async def test_user_flow_rejects_duplicate_output(hass: HomeAssistant) -> None:
    entry = config_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Duplicate", "light": "light.wled", "preset": "select.other"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_saves_sources_and_reloads(hass: HomeAssistant) -> None:
    entry = config_entry()
    with patch.object(LightingController, "async_start"):
        await setup_entry(hass, entry)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {}

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "light": "light.wled",
                "preset": "select.wled",
                "windows": [
                    {"name": "Morning", "start": "Time", "start_time": "06:00:00", "end": "Sunrise"}
                ],
                "sources": [
                    {
                        "name": "Game",
                        "type": "Condition",
                        "entity": "binary_sensor.game",
                        "theme": "Team",
                    }
                ],
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options["windows"] == [
        {
            "name": "Morning",
            "start": "time",
            "start_time": "06:00:00",
            "end": "sunrise",
            "end_offset": 0,
        }
    ]
    assert entry.runtime_data.windows == entry.options["windows"]
    (source,) = entry.options["sources"]
    assert source["id"]
    assert {key: value for key, value in source.items() if key != "id"} == {
        "name": "Game",
        "type": "condition",
        "entity": "binary_sensor.game",
        "theme": "Team",
    }
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_source_{source['id']}")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_registers_entities_and_unloads(hass: HomeAssistant) -> None:
    entry = config_entry()
    with patch.object(LightingController, "request_reconcile"):
        await setup_entry(hass, entry)
        controller = entry.runtime_data

        registry = er.async_get(hass)
        entities = er.async_entries_for_config_entry(registry, entry.entry_id)
        assert {entity.domain for entity in entities} == {
            "binary_sensor",
            "button",
            "number",
            "select",
            "sensor",
        }
        for domain, key in (
            ("select", "mode"),
            ("number", "brightness"),
            ("sensor", "plan"),
            ("button", "reapply"),
        ):
            entity_id = registry.async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
            assert entity_id is not None
            assert hass.states.get(entity_id) is not None

        devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        assert len(devices) == 1
        assert devices[0].identifiers == {(DOMAIN, entry.entry_id)}
        assert controller._unsubs

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert controller._unsubs == []
    assert controller._task is None or controller._task.done()
    for entity in entities:
        state = hass.states.get(entity.entity_id)
        assert state is None or state.state == "unavailable"


async def test_reconcile_service_targets_setup_and_rejects_manual(hass: HomeAssistant) -> None:
    entry = config_entry()
    with patch.object(LightingController, "async_start"):
        await setup_entry(hass, entry)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("select", DOMAIN, f"{entry.entry_id}_mode")
    assert entity_id is not None

    with patch.object(entry.runtime_data, "request_reconcile") as reconcile:
        await hass.services.async_call(DOMAIN, "reconcile", {"entity_id": entity_id}, blocking=True)
        reconcile.assert_called_once_with("service")
        reconcile.reset_mock()
        await hass.services.async_call(DOMAIN, "reconcile", blocking=True)
        reconcile.assert_called_once_with("service")
        reconcile.reset_mock()

        hass.states.async_set("light.unrelated", "off")
        with pytest.raises(ServiceValidationError, match="target isn't"):
            await hass.services.async_call(
                DOMAIN, "reconcile", {"entity_id": "light.unrelated"}, blocking=True
            )
        reconcile.assert_not_called()
        entry.runtime_data.mode = MODE_MANUAL

        with pytest.raises(ServiceValidationError, match="Manual mode"):
            await hass.services.async_call(
                DOMAIN, "reconcile", {"entity_id": entity_id}, blocking=True
            )
        reconcile.assert_not_called()

    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError, match="not set up"):
        await hass.services.async_call(DOMAIN, "reconcile", blocking=True)


async def test_reconcile_requires_target_for_multiple_setups(hass: HomeAssistant) -> None:
    first = config_entry()
    second = config_entry("Back lights", "_back")
    with patch.object(LightingController, "async_start"):
        await setup_entry(hass, first)
        await setup_entry(hass, second)

    with pytest.raises(ServiceValidationError, match="more than one"):
        await hass.services.async_call(DOMAIN, "reconcile", blocking=True)

    device = dr.async_entries_for_config_entry(dr.async_get(hass), second.entry_id)[0]
    with (
        patch.object(first.runtime_data, "request_reconcile") as front,
        patch.object(second.runtime_data, "request_reconcile") as back,
    ):
        await hass.services.async_call(DOMAIN, "reconcile", {"device_id": device.id}, blocking=True)
        front.assert_not_called()
        back.assert_called_once_with("service")

    assert await hass.config_entries.async_unload(first.entry_id)
    assert await hass.config_entries.async_unload(second.entry_id)
