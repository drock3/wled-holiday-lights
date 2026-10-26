"""WLED master power and preset activation regression tests."""

import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from support import integration


class DeviceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        context = integration()
        modules = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.device_module = importlib.import_module(f"{modules.controller.__package__}.device")
        self.registry = MagicMock()
        self.registry.async_get.return_value = None
        registry_patch = patch.object(
            self.device_module.er, "async_get", return_value=self.registry
        )
        registry_patch.start()
        self.addCleanup(registry_patch.stop)
        timeout_patch = patch.object(self.device_module, "CONFIRM_TIMEOUT", 0)
        timeout_patch.start()
        self.addCleanup(timeout_patch.stop)
        self.look = "[Halloween] Orange"
        self.states = {
            "light.wled": SimpleNamespace(state="off", attributes={"brightness": 128}),
            "select.wled": SimpleNamespace(
                state="unknown", attributes={"options": [self.look, "[Christmas] Sparkle"]}
            ),
            "switch.outlet": SimpleNamespace(state="on", attributes={}),
        }
        self.hass = MagicMock()
        self.hass.states.get.side_effect = self.states.get
        self.hass.services.async_call = AsyncMock(side_effect=self.service)
        self.ctl = MagicMock(brightness=128, output={})
        self.ctl.plan_is_current.return_value = True
        self.plan = {"allowed": True, "look": self.look}
        self.preset_turns_off = False
        self.preset_brightness = 128
        self.ignore_turn_on = False
        self.device = self.device_module.LightingDevice(
            self.hass, self.ctl, "light.wled", "select.wled", None
        )

    async def service(self, domain, service, data, *, blocking):
        if domain == "light":
            state = self.states[data["entity_id"]]
            if service == "turn_on" and not self.ignore_turn_on:
                state.state = "on"
                state.attributes["brightness"] = data["brightness"]
            elif service == "turn_off":
                state.state = "off"
        elif domain == "select":
            self.states["select.wled"].state = data["option"]
            self.states["light.wled"].state = "off" if self.preset_turns_off else "on"
            self.states["light.wled"].attributes["brightness"] = self.preset_brightness
        elif domain == "switch":
            self.states[data["entity_id"]].state = "on" if service == "turn_on" else "off"

    async def test_off_preset_at_target_brightness_is_turned_back_on(self):
        self.preset_turns_off = True

        await self.device.async_apply(self.plan)

        self.assertEqual("on", self.states["light.wled"].state)
        self.assertEqual(128, self.states["light.wled"].attributes["brightness"])
        self.ctl.set_output.assert_called_once_with("applied", self.look, look=self.look)
        self.ctl.output_error.assert_not_called()
        commands = [
            (args[0], args[1])
            for args, _kwargs in self.hass.services.async_call.call_args_list
            if args[0] != "homeassistant"
        ]
        self.assertEqual(
            [("light", "turn_on"), ("select", "select_option"), ("light", "turn_on")],
            commands,
        )

    async def test_off_preset_is_corrected_when_changing_looks_while_on(self):
        self.states["light.wled"].state = "on"
        self.ctl.output = {"status": "applied", "look": "[Christmas] Sparkle"}
        self.preset_turns_off = True

        await self.device.async_apply(self.plan)

        self.assertEqual("on", self.states["light.wled"].state)
        self.ctl.set_output.assert_called_once_with("applied", self.look, look=self.look)
        self.ctl.output_error.assert_not_called()

    async def test_preset_brightness_is_overridden(self):
        self.preset_brightness = 42

        await self.device.async_apply(self.plan)

        self.assertEqual(128, self.states["light.wled"].attributes["brightness"])
        self.ctl.set_output.assert_called_once_with("applied", self.look, look=self.look)

    async def test_already_applied_look_does_not_restart_animation(self):
        self.states["light.wled"].state = "on"
        self.ctl.output = {"status": "applied", "look": self.look}

        await self.device.async_apply(self.plan)

        self.hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "update_entity", {"entity_id": "light.wled"}, blocking=True
        )
        self.ctl.set_output.assert_called_once_with("applied", self.look, look=self.look)

    async def test_refresh_revealing_power_off_reapplies_preset(self):
        self.states["light.wled"].state = "on"
        self.ctl.output = {"status": "applied", "look": self.look}

        async def refreshed_service(domain, service, data, *, blocking):
            if domain == "homeassistant":
                self.states["light.wled"].state = "off"
            await self.service(domain, service, data, blocking=blocking)

        self.hass.services.async_call.side_effect = refreshed_service

        await self.device.async_apply(self.plan)

        self.hass.services.async_call.assert_any_await(
            "select",
            "select_option",
            {"entity_id": "select.wled", "option": self.look},
            blocking=True,
        )
        self.assertEqual("on", self.states["light.wled"].state)

    async def test_force_resends_power_preset_and_brightness(self):
        self.states["light.wled"].state = "on"
        self.ctl.output = {"status": "applied", "look": self.look}
        self.device.power = "switch.outlet"

        await self.device.async_apply(self.plan, force=True)

        self.hass.services.async_call.assert_has_awaits(
            [
                call("switch", "turn_on", {"entity_id": "switch.outlet"}, blocking=True),
                call("homeassistant", "update_entity", {"entity_id": "light.wled"}, blocking=True),
                call(
                    "light",
                    "turn_on",
                    {"entity_id": "light.wled", "brightness": 128},
                    blocking=True,
                ),
                call(
                    "select",
                    "select_option",
                    {"entity_id": "select.wled", "option": self.look},
                    blocking=True,
                ),
            ]
        )

    async def test_booting_with_leds_on_still_selects_intended_preset(self):
        self.ctl.output = {"status": "applied", "look": self.look}

        async def booted_service(domain, service, data, *, blocking):
            if domain == "homeassistant":
                self.states["light.wled"].state = "on"
            await self.service(domain, service, data, blocking=blocking)

        self.hass.services.async_call.side_effect = booted_service

        await self.device.async_apply(self.plan)

        self.hass.services.async_call.assert_any_await(
            "select",
            "select_option",
            {"entity_id": "select.wled", "option": self.look},
            blocking=True,
        )
        self.ctl.set_output.assert_called_once_with("applied", self.look, look=self.look)

    async def test_failed_led_power_is_reported_and_outlet_is_cut(self):
        self.device.power = "switch.outlet"
        self.ignore_turn_on = True
        self.preset_turns_off = True

        await self.device.async_apply(self.plan)

        self.assertEqual("off", self.states["switch.outlet"].state)
        self.ctl.output_error.assert_called_once_with(
            "WLED did not confirm intended power and brightness."
        )
        self.assertNotIn(
            "applied", [args[0] for args, _kwargs in self.ctl.set_output.call_args_list]
        )

    def registry_entry(self, entity_id, unique_id, *, platform="wled", entry_id="native"):
        return SimpleNamespace(
            entity_id=entity_id,
            unique_id=unique_id,
            platform=platform,
            config_entry_id=entry_id,
            device_id="controller",
        )

    def resolve_light(self, selected, master):
        entries = {entry.entity_id: entry for entry in (selected, master) if entry}
        self.registry.async_get.side_effect = entries.get
        self.registry.async_get_entity_id.return_value = master.entity_id if master else None
        return self.device_module.LightingDevice(
            self.hass, self.ctl, selected.entity_id, "select.wled", None
        )

    async def test_segment_uses_master_for_power_brightness_and_verification(self):
        selected = self.registry_entry("light.segment", "aa:bb:cc:dd:ee:ff_0")
        master = self.registry_entry("light.wled", "aa:bb:cc:dd:ee:ff")
        self.states["light.segment"] = SimpleNamespace(state="on", attributes={"brightness": 128})
        device = self.resolve_light(selected, master)

        self.assertEqual("light.wled", device.light)
        await device.async_apply(self.plan)

        self.registry.async_get_entity_id.assert_called_with("light", "wled", "aa:bb:cc:dd:ee:ff")
        self.assertEqual("on", self.states["light.wled"].state)
        self.ctl.set_output.assert_called_once_with("applied", self.look, look=self.look)

    def test_single_segment_without_master_keeps_selected_light(self):
        selected = self.registry_entry("light.wled", "aa:bb:cc:dd:ee:ff_0")
        self.assertEqual("light.wled", self.resolve_light(selected, None).light)

    def test_main_light_is_not_replaced(self):
        selected = self.registry_entry("light.wled", "aa:bb:cc:dd:ee:ff")
        self.assertEqual("light.wled", self.resolve_light(selected, None).light)
        self.registry.async_get_entity_id.assert_not_called()

    def test_non_wled_segment_is_not_replaced(self):
        selected = self.registry_entry("light.segment", "aa:bb:cc:dd:ee:ff_0", platform="other")
        master = self.registry_entry("light.wled", "aa:bb:cc:dd:ee:ff")
        self.assertEqual("light.segment", self.resolve_light(selected, master).light)
        self.registry.async_get_entity_id.assert_not_called()

    def test_different_device_is_not_used_as_master(self):
        selected = self.registry_entry("light.segment", "aa:bb:cc:dd:ee:ff_0")
        master = self.registry_entry("light.wled", "aa:bb:cc:dd:ee:ff")
        master.device_id = "other_controller"
        self.assertEqual("light.segment", self.resolve_light(selected, master).light)

    async def test_master_is_used_to_turn_off_led_output(self):
        selected = self.registry_entry("light.segment", "aa:bb:cc:dd:ee:ff_0")
        master = self.registry_entry("light.wled", "aa:bb:cc:dd:ee:ff")
        device = self.resolve_light(selected, master)
        self.states["light.wled"].state = "on"

        self.assertTrue(await device.async_power_off("Outside lighting hours"))

        self.hass.services.async_call.assert_awaited_once_with(
            "light", "turn_off", {"entity_id": "light.wled"}, blocking=True
        )
        self.ctl.set_output.assert_called_once_with("off", "Outside lighting hours")

    async def test_manual_only_turns_on_outlet_not_master_leds(self):
        selected = self.registry_entry("light.segment", "aa:bb:cc:dd:ee:ff_0")
        master = self.registry_entry("light.wled", "aa:bb:cc:dd:ee:ff")
        device = self.resolve_light(selected, master)
        device.power = "switch.outlet"
        self.states["switch.outlet"].state = "off"

        await device.async_power_for_manual()

        self.hass.services.async_call.assert_awaited_once_with(
            "switch", "turn_on", {"entity_id": "switch.outlet"}, blocking=True
        )
        self.assertEqual("off", self.states["light.wled"].state)
        self.ctl.output_error.assert_not_called()

    def test_other_integrations_and_controllers_are_not_replaced(self):
        for platform, entry_id in (("other", "native"), ("wled", "different")):
            with self.subTest(platform=platform, entry_id=entry_id):
                selected = self.registry_entry("light.segment", "aa:bb:cc:dd:ee:ff_0")
                master = self.registry_entry(
                    "light.wled", "aa:bb:cc:dd:ee:ff", platform=platform, entry_id=entry_id
                )
                self.assertEqual("light.segment", self.resolve_light(selected, master).light)
