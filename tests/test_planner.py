"""Planning rules run without Home Assistant or any third-party dependencies."""

from datetime import datetime, timedelta, timezone
import importlib.util
import unittest

from support import COMPONENT

spec = importlib.util.spec_from_file_location("planner", COMPONENT / "planner.py")
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 20, 19, tzinfo=timezone.utc)
        self.library = planner.parse_library(
            [
                "[Christmas] Sparkle",
                "[Christmas] Warm",
                "[Team] Colors",
            ]
        )

    def source(self, name, themes, *, enabled=True, available=True):
        return {
            "id": name,
            "name": name,
            "type": "calendar",
            "enabled": enabled,
            "available": available,
            "warnings": [],
            "requests": [{"theme": theme} for theme in themes],
        }

    def plan(self, sources, **overrides):
        return planner.build_plan(
            **{
                "now": self.now,
                "library": self.library,
                "enabled": True,
                "sources": sources,
                "hours": True,
                "power_ok": True,
                **overrides,
            }
        )

    def test_first_matching_source_wins(self):
        plan = self.plan(
            [
                self.source("Games", ["Missing", "Team"]),
                self.source("Holidays", ["Christmas"]),
            ]
        )
        self.assertEqual("Games", plan["source"])
        self.assertEqual("Team", plan["theme"])
        self.assertEqual("[Team] Colors", plan["look"])
        self.assertTrue(plan["allowed"])
        self.assertEqual(
            ["no preset", "won", "lower priority"],
            [request["status"] for request in plan["requests"]],
        )

    def test_disabled_or_unmatched_source_does_not_block_lower_priority(self):
        plan = self.plan(
            [
                self.source("Disabled", ["Team"], enabled=False),
                self.source("Unknown theme", ["Missing"]),
                self.source("Holidays", ["Christmas"]),
            ]
        )
        self.assertEqual("Holidays", plan["source"])
        self.assertEqual("Disabled", plan["sources"][0]["reason"])

    def test_activation_gates_keep_output_off(self):
        for gate in ("enabled", "hours", "power_ok"):
            with self.subTest(gate=gate):
                plan = self.plan([self.source("Games", ["Team"])], **{gate: False})
                self.assertFalse(plan["allowed"])
        self.assertFalse(self.plan([])["allowed"])

    def test_missing_preset_cache_is_not_treated_as_invalid_theme(self):
        plan = self.plan(
            [self.source("Games", ["Team"])],
            library=planner.parse_library([]),
            presets_loaded=False,
        )
        self.assertFalse(plan["allowed"])
        self.assertEqual("Waiting for WLED presets", plan["reason"])
        self.assertEqual("presets not loaded", plan["requests"][0]["status"])

    def test_looks_rotate_daily_independent_of_preset_order(self):
        source = [self.source("Holidays", ["Christmas"])]
        today = self.plan(source)["look"]
        tomorrow = self.plan(source, now=self.now + timedelta(days=1))["look"]
        self.assertNotEqual(today, tomorrow)
        reordered = planner.parse_library(
            list(
                reversed(
                    [
                        "[Christmas] Sparkle",
                        "[Christmas] Warm",
                        "[Team] Colors",
                    ]
                )
            )
        )
        self.assertEqual(today, self.plan(source, library=reordered)["look"])

    def spans(self, *windows, sunrise=7, sunset=18):
        sun = {
            "sunrise": None if sunrise is None else self.now.replace(hour=sunrise),
            "sunset": None if sunset is None else self.now.replace(hour=sunset),
        }
        return planner.window_spans(list(windows), self.now.date(), timezone.utc, sun)

    def at(self, hour, minute=0):
        return self.now.replace(hour=hour, minute=minute)

    def test_sun_window_applies_offset_and_clock_midnight_ends_the_day(self):
        evening = {
            "name": "Evening",
            "start": "sunset",
            "start_offset": -30,
            "end": "time",
            "end_time": "00:00:00",
        }
        (span,) = self.spans(evening)
        self.assertEqual("Evening", span["name"])
        self.assertEqual(self.at(17, 30), span["start"])
        self.assertEqual(self.at(0) + timedelta(days=1), span["end"])
        self.assertFalse(planner.lighting_hours(self.at(17, 29), [span]))
        self.assertTrue(planner.lighting_hours(self.at(17, 30), [span]))
        self.assertTrue(planner.lighting_hours(self.at(23, 59), [span]))
        self.assertFalse(planner.lighting_hours(span["end"], [span]))

    def test_morning_and_evening_windows_wake_at_each_boundary(self):
        morning = {
            "name": "Morning",
            "start": "time",
            "start_time": "06:00:00",
            "end": "sunrise",
            "end_offset": 15,
        }
        evening = {
            "name": "Evening",
            "start": "sunset",
            "end": "time",
            "end_time": "23:30:00",
        }
        spans = self.spans(morning, evening)
        expected = [
            (self.at(0), False, self.at(6)),
            (self.at(6), True, self.at(7, 15)),
            (self.at(7, 15), False, self.at(18)),
            (self.at(18), True, self.at(23, 30)),
            (self.at(23, 30), False, None),
        ]
        for now, hours, change in expected:
            with self.subTest(now=now):
                self.assertEqual(hours, planner.lighting_hours(now, spans))
                self.assertEqual(change, planner.next_hours_change(now, spans))

    def test_overlapping_windows_combine(self):
        spans = self.spans(
            {"start": "time", "start_time": "17:00", "end": "time", "end_time": "20:00"},
            {"start": "sunset", "end": "time", "end_time": "22:00"},
        )
        for hour in (17, 18, 19, 20, 21):
            with self.subTest(hour=hour):
                self.assertTrue(planner.lighting_hours(self.at(hour), spans))
        self.assertFalse(planner.lighting_hours(self.at(22), spans))

    def test_windows_stay_within_the_day(self):
        all_day = {"start": "time", "start_time": "00:00", "end": "time", "end_time": "00:00"}
        late = {"start": "sunset", "start_offset": 240, "end": "sunset", "end_offset": 240}
        past_midnight = {"start": "sunset", "end": "sunset", "end_offset": 240}
        all_day_span, late_span, past_span = self.spans(all_day, late, past_midnight, sunset=21)
        self.assertEqual(self.at(0), all_day_span["start"])
        self.assertEqual(self.at(0) + timedelta(days=1), all_day_span["end"])
        self.assertIsNone(late_span["start"])
        self.assertEqual(self.at(21), past_span["start"])
        self.assertEqual(self.at(0) + timedelta(days=1), past_span["end"])

    def test_windows_that_cannot_open_stay_closed(self):
        windows = [
            {"name": "No sun", "start": "sunset", "end": "time", "end_time": "00:00"},
            {"name": "Backwards", "start": "time", "start_time": "06:00", "end": "sunrise"},
            {"name": "Bad time", "start": "time", "start_time": "late", "end": "sunset"},
            {"name": "Bad type", "start": "noon", "end": "sunset"},
        ]
        spans = self.spans(*windows, sunrise=5, sunset=None)
        self.assertEqual(
            ["No sun", "Backwards", "Bad time", "Bad type"], [s["name"] for s in spans]
        )
        for span in spans:
            with self.subTest(window=span["name"]):
                self.assertIsNone(span["start"])
                self.assertIsNone(span["end"])
        self.assertFalse(planner.lighting_hours(self.at(20), spans))
        self.assertIsNone(planner.next_hours_change(self.at(0), spans))

    def test_calendar_requests_choose_shortest_active_event_and_exact_boundary(self):
        events = [
            {"summary": "Christmas", "start": "2026-10-20", "end": "2026-10-21"},
            {
                "summary": "Team",
                "start": "2026-10-20T18:00:00+00:00",
                "end": "2026-10-20T20:00:00+00:00",
            },
            {"summary": "Tomorrow", "start": "2026-10-21", "end": "2026-10-22"},
        ]
        requests, warnings = planner.calendar_requests(events, self.now)
        self.assertEqual(["Team", "Christmas"], [request["theme"] for request in requests])
        self.assertEqual([], warnings)
        self.assertEqual(self.now.replace(hour=20), planner.next_boundary(events, self.now))

    def test_invalid_calendar_events_are_reported_and_ignored(self):
        events = [
            {"summary": "Naive", "start": "2026-10-20T18:00:00", "end": "2026-10-20T20:00:00"},
            {"summary": "Backwards", "start": "2026-10-21", "end": "2026-10-20"},
        ]
        requests, warnings = planner.calendar_requests(events, self.now)
        self.assertEqual([], requests)
        self.assertEqual(2, len(warnings))
        self.assertIsNone(planner.next_boundary(events, self.now))

    def test_reserved_themes_and_capitalization_conflicts_are_reported(self):
        library = planner.parse_library(
            [
                "[Auto] Reserved",
                "[Off] Reserved",
                "[Team] Colors",
                "[team] Other",
                "[Bad]",
            ]
        )
        self.assertEqual(3, len(library["warnings"]))
        self.assertEqual(["Theme capitalization conflict: team"], library["errors"])
        self.assertFalse(self.plan([self.source("Games", ["Team"])], library=library)["allowed"])
