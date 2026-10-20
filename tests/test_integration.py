"""Fresh-release configuration, persistence, and lifecycle regression tests."""

import asyncio
from datetime import datetime, time, timedelta, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from support import COMPONENT, integration

EVENING = {
    "name": "Evening",
    "start": "sunset",
    "start_offset": 0,
    "end": "time",
    "end_time": "00:00:00",
}
EVENING_ITEM = {
    "name": "Evening",
    "start": "Sunset",
    "start_offset": 0,
    "end": "Time",
    "end_time": "00:00:00",
}


class ConfigurationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = integration()
        self.modules = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.flow = self.modules.flow

    def test_release_uses_semver_and_version_one_config_schema_without_migration_hook(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text())
        self.assertRegex(manifest["version"], r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
        self.assertEqual(1, self.flow.HolidayLightingConfigFlow.VERSION)
        self.assertFalse(hasattr(self.modules.setup, "async_migrate_entry"))

    async def test_setup_creates_empty_source_list_and_sunset_to_midnight_window(self):
        flow = self.flow.HolidayLightingConfigFlow()
        flow._async_current_entries = MagicMock(return_value=[])
        flow.async_create_entry = MagicMock(side_effect=lambda **kwargs: kwargs)

        result = await flow.async_step_user(
            {
                "name": " Front lights ",
                "light": "light.wled",
                "preset": "select.wled",
            }
        )

        self.assertEqual(
            {
                "title": "Front lights",
                "data": {"light": "light.wled", "preset": "select.wled", "power": None},
                "options": {"sources": [], "windows": [EVENING]},
            },
            result,
        )

    async def test_setup_rejects_duplicate_output(self):
        flow = self.flow.HolidayLightingConfigFlow()
        flow._async_current_entries = MagicMock(
            return_value=[
                SimpleNamespace(
                    entry_id="existing",
                    data={"light": "light.wled", "preset": "select.other"},
                    options={},
                ),
            ]
        )
        flow.async_abort = MagicMock(side_effect=lambda **kwargs: kwargs)

        result = await flow.async_step_user(
            {
                "name": "Lights",
                "light": "light.wled",
                "preset": "select.wled",
            }
        )

        self.assertEqual({"reason": "already_configured"}, result)

    def test_all_source_types_round_trip_through_one_schema(self):
        items = [
            {"name": "Holidays", "type": "Calendar", "entity": "calendar.holidays"},
            {"name": "Games", "type": "Condition", "entity": "binary_sensor.game", "theme": "Team"},
            {"name": "Custom", "type": "Template", "template": "{{ 'Christmas' }}"},
        ]
        self.flow.ulid_now = MagicMock(side_effect=["calendar", "condition", "template"])

        sources, error, name = self.flow._parse_sources(MagicMock(), items, [])

        self.assertIsNone(error)
        self.assertEqual("", name)
        self.assertEqual(items, [self.flow._to_item(source) for source in sources])
        calendar = self.modules.sources.create_source(MagicMock(), sources[0])
        self.assertEqual("calendar.holidays", calendar.calendar)
        self.assertNotIn("calendar", sources[0])
        condition = self.modules.sources.create_source(MagicMock(), sources[1])
        self.assertEqual("binary_sensor.game", condition.entity)
        self.assertEqual("Team", condition.theme)
        hass = MagicMock()
        self.modules.sources.create_source(hass, sources[2])
        self.modules.sources.Template.assert_called_with("{{ 'Christmas' }}", hass)

    def test_source_ids_survive_reordering_and_renaming(self):
        existing = [
            {"id": "a", "name": "Holidays", "type": "calendar", "entity": "calendar.holidays"},
            {
                "id": "b",
                "name": "Games",
                "type": "condition",
                "entity": "sensor.game",
                "theme": "Team",
            },
        ]
        items = [
            {"name": "Games", "type": "Condition", "entity": "sensor.game", "theme": "New team"},
            {"name": "Calendar renamed", "type": "Calendar", "entity": "calendar.holidays"},
        ]

        sources, error, _ = self.flow._parse_sources(MagicMock(), items, existing)

        self.assertIsNone(error)
        self.assertEqual(["b", "a"], [source["id"] for source in sources])

    def test_invalid_sources_are_reported(self):
        cases = [
            ({"name": "", "type": "Calendar"}, "source_name_required"),
            (
                {"name": "Bad", "type": "Calendar", "entity": "light.wled"},
                "source_calendar_required",
            ),
            (
                {"name": "Bad", "type": "Condition", "entity": "sensor.game"},
                "source_condition_required",
            ),
            ({"name": "Bad", "type": "Template", "template": " "}, "source_template_required"),
        ]
        for item, expected in cases:
            with self.subTest(expected=expected):
                sources, error, _ = self.flow._parse_sources(MagicMock(), [item], [])
                self.assertEqual([], sources)
                self.assertEqual(expected, error)

    def test_windows_round_trip_keeping_only_the_fields_each_end_uses(self):
        items = [
            {
                "name": " Morning ",
                "start": "Time",
                "start_time": "06:00",
                "start_offset": 30,
                "end": "Sunrise",
                "end_offset": 15.0,
                "end_time": "09:00:00",
            },
            {"start": "Sunset", "end": "Time", "end_time": "00:00:00"},
        ]

        windows, error, name = self.flow._parse_windows(items)

        self.assertIsNone(error)
        self.assertEqual("", name)
        self.assertEqual(
            [
                {
                    "name": "Morning",
                    "start": "time",
                    "start_time": "06:00:00",
                    "end": "sunrise",
                    "end_offset": 15,
                },
                {**EVENING, "name": "Window 2"},
            ],
            windows,
        )
        self.assertEqual(
            [
                {
                    "name": "Morning",
                    "start": "Time",
                    "start_time": "06:00:00",
                    "end": "Sunrise",
                    "end_offset": 15,
                },
                {**EVENING_ITEM, "name": "Window 2"},
            ],
            [self.flow._window_item(window) for window in windows],
        )

    def test_invalid_windows_are_reported(self):
        clock = {"start": "Time", "start_time": "18:00", "end": "Time"}
        cases = [
            ([], "window_required"),
            ([{"name": "Bad", "start": "Sunset"}], "window_point_required"),
            ([{"name": "Bad", "start": "Noon", "end": "Sunset"}], "window_point_required"),
            ([{**clock, "name": "Bad", "end_time": "late"}], "window_time_required"),
            ([{**clock, "name": "Bad", "end_time": "18:00"}], "window_backwards"),
            ([{**clock, "name": "Bad", "end_time": "06:00"}], "window_backwards"),
            (
                [{"name": "Bad", "start": "Sunset", "start_offset": 241, "end": "Sunrise"}],
                "window_offset_invalid",
            ),
        ]
        for items, expected in cases:
            with self.subTest(expected=expected, items=items):
                windows, error, _ = self.flow._parse_windows(items)
                self.assertEqual([], windows)
                self.assertEqual(expected, error)
        windows, error, _ = self.flow._parse_windows([{**clock, "end_time": "00:00"}])
        self.assertIsNone(error)
        self.assertEqual("00:00:00", windows[0]["end_time"])

    async def test_unchanged_options_do_not_add_redundant_output_settings(self):
        flow = self.flow.HolidayLightingOptionsFlow()
        flow.config_entry = SimpleNamespace(
            entry_id="setup",
            data={"light": "light.wled", "preset": "select.wled", "power": None},
            options={"sources": [], "windows": [EVENING]},
        )
        flow.hass = MagicMock()
        flow.hass.config_entries.async_entries.return_value = [flow.config_entry]
        flow.async_create_entry = MagicMock(side_effect=lambda **kwargs: kwargs)

        result = await flow.async_step_init(
            {"light": "light.wled", "preset": "select.wled", "windows": [EVENING_ITEM]}
        )

        self.assertEqual({"data": {"sources": [], "windows": [EVENING]}}, result)

    async def test_options_form_shows_default_window_for_setups_without_windows(self):
        flow = self.flow.HolidayLightingOptionsFlow()
        flow.config_entry = SimpleNamespace(
            entry_id="setup",
            data={"light": "light.wled", "preset": "select.wled", "power": None},
            options={"sources": []},
        )
        flow.add_suggested_values_to_schema = MagicMock(side_effect=lambda schema, values: values)
        flow.async_show_form = MagicMock(side_effect=lambda **kwargs: kwargs)

        result = await flow.async_step_init()

        self.assertEqual([EVENING_ITEM], result["data_schema"]["windows"])

    async def test_clearing_outlet_overrides_original_setup(self):
        flow = self.flow.HolidayLightingOptionsFlow()
        flow.config_entry = SimpleNamespace(
            entry_id="setup",
            data={"light": "light.wled", "preset": "select.wled", "power": "switch.outlet"},
            options={"sources": [], "windows": [EVENING]},
        )
        flow.hass = MagicMock()
        flow.hass.config_entries.async_entries.return_value = [flow.config_entry]
        flow.async_create_entry = MagicMock(side_effect=lambda **kwargs: kwargs)

        result = await flow.async_step_init(
            {"light": "light.wled", "preset": "select.wled", "windows": [EVENING_ITEM]}
        )

        self.assertEqual({"data": {"power": None, "sources": [], "windows": [EVENING]}}, result)


class ManualOverrideTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = integration()
        self.modules = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.now = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)
        clock = patch.object(self.modules.controller.dt_util, "now", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        entry = SimpleNamespace(
            entry_id="setup",
            title="Lights",
            data={"light": "light.wled", "preset": "select.wled"},
            options={
                "sources": [
                    {
                        "id": "packers",
                        "name": "Packers",
                        "type": "template",
                        "template": "{{ 'Packers' }}",
                    },
                    {
                        "id": "calendar",
                        "name": "Holidays",
                        "type": "calendar",
                        "entity": "calendar.holidays",
                    },
                ]
            },
            async_create_background_task=lambda _hass, work, _name: asyncio.create_task(work),
        )
        self.controller = self.modules.controller.LightingController(MagicMock(), entry)
        ctl = self.controller
        ctl._save = MagicMock()
        ctl._updated = MagicMock()
        ctl._update_hours = MagicMock()
        ctl.hours = True
        ctl.device = MagicMock(
            power=None,
            transitioning=False,
            preset_options=["[Packers] Green and gold", "[Halloween] Orange"],
            async_power_for_manual=AsyncMock(),
            async_power_on=AsyncMock(),
            async_power_off=AsyncMock(),
            async_apply=AsyncMock(),
        )
        self.template, self.calendar = ctl.sources
        self.template.template.async_render.return_value = "Packers"
        self.calendar.async_refresh = AsyncMock(return_value=True)
        self.calendar.async_evaluate = AsyncMock(
            return_value=self.calendar._result([{"theme": "Halloween"}])
        )
        self.calendar.next_change = MagicMock(return_value=self.now + timedelta(hours=1))

    async def test_restored_manual_mode_populates_schedule_inside_and_outside_hours(self):
        ctl = self.controller
        ctl.mode = "Manual"
        ctl.manual_date = self.now.date().isoformat()
        ctl.output = {"status": "manual", "detail": "Manual override"}

        for hours in (True, False):
            with self.subTest(hours=hours):
                ctl.hours = hours
                await ctl._ha_started(ctl.hass)
                if ctl._task:
                    await ctl._task

                self.assertIsNotNone(ctl.plan)
                self.assertEqual("Packers", ctl.plan["theme"])
                self.assertEqual("[Packers] Green and gold", ctl.plan["look"])
                self.assertEqual("Packers", ctl.plan["source"])
                self.assertEqual("Packers: Packers", ctl.plan["reason"])
                self.assertEqual(self.now.isoformat(), ctl.plan["evaluated"])
                self.assertEqual(2, len(ctl.plan["requests"]))
                self.assertEqual(
                    ["Packers", "Halloween"], [source["theme"] for source in ctl.plan["sources"]]
                )
                self.assertEqual([], ctl.plan["errors"])
                self.assertEqual([], ctl.plan["warnings"])
                self.assertTrue(ctl.plan["enabled"])
                self.assertEqual(hours, ctl.plan["allowed"])
                self.assertFalse(ctl.plan_is_current(ctl.plan))
                self.assertIsNotNone(ctl._boundary_unsub)
                self.assertEqual("manual", ctl.output["status"])

        ctl.device.async_power_for_manual.assert_not_awaited()
        ctl.device.async_power_on.assert_not_awaited()
        ctl.device.async_power_off.assert_not_awaited()
        ctl.device.async_apply.assert_not_awaited()

    async def test_entering_manual_powers_outlet_once_and_keeps_sources_current(self):
        ctl = self.controller
        ctl._started = True
        ctl.set_mode("Manual")
        await ctl._task

        self.assertIsNotNone(ctl.plan)
        self.assertEqual("Packers", ctl.plan["theme"])
        ctl.device.async_power_for_manual.assert_awaited_once()

        self.template.template.async_render.return_value = ""
        ctl._source_changed("Packers")
        await ctl._task

        self.assertEqual("Halloween", ctl.plan["theme"])
        self.assertEqual("No active requests", ctl.plan["sources"][0]["reason"])
        ctl._calendar_refresh(self.now)
        await ctl._task
        self.calendar.async_refresh.assert_awaited_once()
        self.template.template.async_render.return_value = "Packers"
        ctl._source_boundary(self.now)
        await ctl._task
        self.assertEqual("Packers", ctl.plan["theme"])
        ctl.set_source_enabled("packers", False)
        await ctl._task
        self.assertEqual("Halloween", ctl.plan["theme"])
        ctl.device.async_power_for_manual.assert_awaited_once()
        ctl.device.async_power_on.assert_not_awaited()
        ctl.device.async_power_off.assert_not_awaited()
        ctl.device.async_apply.assert_not_awaited()

    async def test_manual_mode_does_not_power_on_to_discover_presets(self):
        ctl = self.controller
        ctl.mode = "Manual"
        ctl._started = True
        ctl.device.preset_options = None
        ctl.request_reconcile("start", force=True)
        if ctl._task:
            await ctl._task

        self.assertIsNotNone(ctl.plan)
        self.assertEqual("Waiting for WLED presets", ctl.plan["reason"])
        self.assertFalse(ctl._force_pending)
        ctl.device.async_power_on.assert_not_awaited()
        ctl.device.async_power_off.assert_not_awaited()
        ctl.device.async_apply.assert_not_awaited()

    async def test_manual_mode_uses_cached_presets_when_wled_is_offline(self):
        ctl = self.controller
        ctl.mode = "Manual"
        ctl.presets = ["[Packers] Cached look"]
        ctl.device.preset_options = None
        await ctl._reconcile()

        self.assertEqual("Packers", ctl.plan["theme"])
        self.assertEqual("[Packers] Cached look", ctl.plan["look"])
        self.assertEqual(["Packers"], ctl.themes)
        ctl.device.async_power_on.assert_not_awaited()
        ctl.device.async_power_off.assert_not_awaited()
        ctl.device.async_apply.assert_not_awaited()

    async def test_source_change_during_manual_entry_preserves_outlet_startup(self):
        ctl = self.controller
        ctl._started = True
        powering = asyncio.Event()
        finish = asyncio.Event()

        async def power_for_manual():
            powering.set()
            await finish.wait()

        ctl.device.async_power_for_manual.side_effect = power_for_manual
        ctl.set_mode("Manual")
        await powering.wait()
        ctl._source_changed("Packers")
        finish.set()
        await ctl._task

        self.assertEqual(2, ctl.device.async_power_for_manual.await_count)
        self.assertFalse(ctl._manual_power_pending)
        self.assertEqual("Packers", ctl.plan["theme"])
        ctl.device.async_power_off.assert_not_awaited()
        ctl.device.async_apply.assert_not_awaited()

    async def test_unavailable_sources_retry_in_manual_without_touching_output(self):
        ctl = self.controller
        ctl.mode = "Manual"
        ctl._started = True
        self.calendar.async_evaluate.return_value = self.calendar._result(
            available=False, warnings=["Calendar unavailable"]
        )
        await ctl._reconcile()

        self.assertEqual(["Holidays: Calendar unavailable"], ctl.plan["warnings"])
        self.assertIsNotNone(ctl._retry_unsub)
        ctl._retry(self.now)
        await ctl._task
        ctl.device.async_power_for_manual.assert_not_awaited()
        ctl.device.async_power_off.assert_not_awaited()
        ctl.device.async_apply.assert_not_awaited()

    async def test_automatic_and_disabled_still_control_hardware(self):
        ctl = self.controller
        ctl._started = True
        for mode, hours, power_off in (
            ("Automatic", True, False),
            ("Automatic", False, True),
            ("Disabled", True, True),
        ):
            with self.subTest(mode=mode, hours=hours):
                ctl.mode = mode
                ctl.hours = hours
                ctl.device.async_power_off.reset_mock()
                ctl.device.async_apply.reset_mock()
                await ctl._reconcile()

                self.assertEqual(mode == "Automatic", ctl.plan["enabled"])
                self.assertEqual(power_off, ctl.device.async_power_off.await_count == 1)
                ctl.device.async_apply.assert_awaited_once_with(ctl.plan, force=False)

    async def test_leaving_manual_resumes_hardware_control(self):
        ctl = self.controller
        ctl.mode = "Manual"
        ctl._started = True
        ctl.set_mode("Automatic")
        await ctl._task

        self.assertEqual("Packers", ctl.plan["theme"])
        self.assertTrue(ctl.plan_is_current(ctl.plan))
        ctl.device.async_apply.assert_awaited_once_with(ctl.plan, force=False)

    async def test_midnight_ends_manual_override_and_applies_schedule(self):
        ctl = self.controller
        ctl.mode = "Manual"
        ctl.manual_date = (self.now - timedelta(days=1)).date().isoformat()
        ctl._started = True
        ctl._midnight(self.now)
        await ctl._task

        self.assertEqual("Automatic", ctl.mode)
        self.assertEqual("", ctl.manual_date)
        self.assertEqual("Packers", ctl.plan["theme"])
        ctl.device.async_apply.assert_awaited_once_with(ctl.plan, force=False)


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = integration()
        self.modules = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.entry = SimpleNamespace(
            entry_id="setup",
            title="Lights",
            data={"light": "light.wled", "preset": "select.wled"},
            options={
                "sources": [
                    {
                        "id": "current",
                        "name": "Holidays",
                        "type": "calendar",
                        "entity": "calendar.holidays",
                    },
                ]
            },
        )
        self.controller = self.modules.controller.LightingController(MagicMock(), self.entry)
        self.controller._store = SimpleNamespace(async_load=AsyncMock(return_value=None))

    async def test_fresh_install_uses_defaults(self):
        await self.controller.async_load()

        self.assertEqual("Automatic", self.controller.mode)
        self.assertEqual("", self.controller.manual_date)
        self.assertEqual(50, self.controller.brightness_pct)
        self.assertEqual({"current": True}, self.controller.source_enabled)
        self.assertEqual([], self.controller.presets)
        self.assertIsNone(self.controller.output["status"])

    async def test_current_state_round_trips_and_drops_deleted_source_flags(self):
        state = {
            "mode": "Manual",
            "manual_date": "2026-10-20",
            "brightness_pct": 73,
            "presets": ["[Christmas] Sparkle"],
            "output": {
                "status": "applied",
                "detail": "Verified",
                "look": "[Christmas] Sparkle",
                "checked": "2026-10-20T18:00:00+00:00",
            },
            "source_enabled": {"current": False, "removed": True},
        }
        self.controller._store.async_load.return_value = state

        await self.controller.async_load()

        self.assertEqual(
            {**state, "source_enabled": {"current": False}}, self.controller._snapshot()
        )
        self.assertEqual(["Christmas"], self.controller.themes)

    async def test_brightness_boundaries_restore_exactly(self):
        for brightness in (1, 100):
            with self.subTest(brightness=brightness):
                self.controller._store.async_load.return_value = {"brightness_pct": brightness}
                await self.controller.async_load()
                self.assertEqual(brightness, self.controller.brightness_pct)

    def test_windows_come_from_options_with_sunset_to_midnight_default(self):
        self.assertEqual([EVENING], self.controller.windows)
        morning = {"name": "Morning", "start": "time", "start_time": "06:00:00", "end": "sunrise"}
        self.entry.options["windows"] = [morning]
        controller = self.modules.controller.LightingController(MagicMock(), self.entry)
        self.assertEqual([morning], controller.windows)

    def test_lighting_hours_follow_windows_and_wake_at_the_next_change(self):
        module = self.modules.controller
        self.controller.windows = [
            {"name": "Morning", "start": "time", "start_time": "06:00", "end": "sunrise"},
            {"name": "Evening", "start": "sunset", "start_offset": -10, "end": "sunset"},
        ]
        self.controller._updated = MagicMock()
        self.controller.request_reconcile = MagicMock()
        day = datetime(2026, 10, 20, tzinfo=timezone.utc)
        sun = {"sunrise": day.replace(hour=7), "sunset": day.replace(hour=18)}
        track = MagicMock()

        def update(hour):
            with (
                patch.object(module.dt_util, "now", return_value=day.replace(hour=hour)),
                patch.object(module.dt_util, "as_local", side_effect=lambda moment: moment),
                patch.object(
                    module,
                    "get_astral_event_date",
                    side_effect=lambda _hass, event, _date: sun[event],
                ),
                patch.object(module, "SUN_EVENT_SUNRISE", "sunrise"),
                patch.object(module, "SUN_EVENT_SUNSET", "sunset"),
                patch.object(module, "async_track_point_in_time", track),
            ):
                self.controller._update_hours()

        update(6)
        self.assertTrue(self.controller.hours)
        self.assertEqual(day.replace(hour=7), self.controller.next_hours_change)
        self.assertEqual(day.replace(hour=7), track.call_args.args[2])
        self.controller.request_reconcile.assert_called_once_with("lighting hours")

        sun["sunset"] = None
        update(12)
        self.assertFalse(self.controller.hours)
        self.assertIsNone(self.controller.next_hours_change)
        self.assertEqual(
            [None, None], [self.controller.spans[1]["start"], self.controller.spans[1]["end"]]
        )
        self.assertIsNone(self.controller._hours_unsub)

    async def test_manual_mode_expires_only_after_its_local_date(self):
        self.controller.mode = "Manual"
        self.controller.manual_date = "2026-10-20"
        self.controller._save = MagicMock()
        self.controller._updated = MagicMock()
        clock = self.modules.controller.dt_util
        with patch.object(
            clock, "now", return_value=datetime(2026, 10, 20, 23, tzinfo=timezone.utc)
        ):
            self.controller._expire_manual()
            self.assertEqual("Manual", self.controller.mode)
        with patch.object(clock, "now", return_value=datetime(2026, 10, 21, tzinfo=timezone.utc)):
            self.controller._expire_manual()
        self.assertEqual("Automatic", self.controller.mode)
        self.assertEqual("", self.controller.manual_date)
        self.controller._save.assert_called_once()

    def test_midnight_returns_manual_to_automatic_when_a_window_also_closes(self):
        module = self.modules.controller
        self.controller._save = MagicMock()
        self.controller._updated = MagicMock()
        calls = []
        self.controller.request_reconcile = MagicMock(
            side_effect=lambda reason, **_: calls.append((reason, self.controller.mode))
        )
        evening = datetime(2026, 10, 20, 23, tzinfo=timezone.utc)
        midnight = datetime(2026, 10, 21, tzinfo=timezone.utc)
        clock = MagicMock(return_value=evening)
        time_change = MagicMock()
        point_in_time = MagicMock()
        with (
            patch.object(module.dt_util, "now", clock),
            patch.object(module.dt_util, "as_local", side_effect=lambda moment: moment),
            patch.object(
                module,
                "get_astral_event_date",
                side_effect=lambda _hass, _event, day: datetime.combine(
                    day, time(18), timezone.utc
                ),
            ),
            patch.object(module, "async_track_time_change", time_change),
            patch.object(module, "async_track_point_in_time", point_in_time),
        ):
            self.controller.async_start()
            (midnight_callback,) = [
                call.args[1]
                for call in time_change.call_args_list
                if call.kwargs == {"hour": 0, "minute": 0, "second": 0}
            ]
            for window_first in (True, False):
                with self.subTest(window_first=window_first):
                    self.controller.mode = "Manual"
                    self.controller.manual_date = "2026-10-20"
                    calls.clear()
                    clock.return_value = evening
                    self.controller._update_hours(trigger=False)
                    self.assertTrue(self.controller.hours)
                    window_callback, closes = point_in_time.call_args.args[1:]
                    self.assertEqual(midnight, closes)

                    clock.return_value = midnight
                    callbacks = [window_callback, midnight_callback]
                    for callback in callbacks if window_first else reversed(callbacks):
                        callback(midnight)

                    self.assertEqual("Automatic", self.controller.mode)
                    self.assertEqual("", self.controller.manual_date)
                    self.assertFalse(self.controller.hours)
                    self.assertIn(("midnight", "Automatic"), calls)

    async def test_setup_removes_only_deleted_source_entities(self):
        registry = MagicMock()
        self.modules.setup.er.async_get.return_value = registry
        self.modules.setup.er.async_entries_for_config_entry.return_value = [
            SimpleNamespace(domain=domain, unique_id=unique_id, entity_id=entity_id)
            for domain, unique_id, entity_id in (
                ("switch", "setup_source_current", "switch.current"),
                ("sensor", "setup_source_current", "sensor.current"),
                ("switch", "setup_source_deleted", "switch.deleted"),
                ("sensor", "setup_source_deleted", "sensor.deleted"),
                ("sensor", "setup_output", "sensor.output"),
            )
        ]
        hass = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        entry = MagicMock(entry_id="setup", options=self.entry.options)
        controller = MagicMock()
        controller.async_load = AsyncMock()

        with patch.object(self.modules.setup, "LightingController", return_value=controller):
            result = await self.modules.setup.async_setup_entry(hass, entry)

        self.assertTrue(result)
        self.assertEqual(
            [("switch.deleted",), ("sensor.deleted",)],
            [call.args for call in registry.async_remove.call_args_list],
        )
        controller.async_load.assert_awaited_once()
        controller.async_start.assert_called_once()
        self.assertIs(controller, entry.runtime_data)
