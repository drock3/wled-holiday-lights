"""Config flow for WLED Holiday Lights: setup, then windows, sources and settings on one form."""

from __future__ import annotations

from datetime import time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    ObjectSelector,
    ObjectSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
    TimeSelector,
)
from homeassistant.helpers.template import Template
from homeassistant.util.ulid import ulid_now

from . import planner
from .const import (
    CONF_ENTITY,
    CONF_ID,
    CONF_LIGHT,
    CONF_NAME,
    CONF_POWER,
    CONF_PRESET,
    CONF_SOURCES,
    CONF_TEMPLATE,
    CONF_THEME,
    CONF_TYPE,
    CONF_WINDOWS,
    DEFAULT_WINDOWS,
    DOMAIN,
    MAX_WINDOW_OFFSET,
    MIN_WINDOW_OFFSET,
    NAME,
    SOURCE_CALENDAR,
    SOURCE_CONDITION,
    SOURCE_TEMPLATE,
    SETTINGS,
)


OUTPUT_SCHEMA = {
    vol.Required(CONF_LIGHT): EntitySelector(EntitySelectorConfig(domain="light")),
    vol.Required(CONF_PRESET): EntitySelector(EntitySelectorConfig(domain="select")),
    vol.Optional(CONF_POWER): EntitySelector(EntitySelectorConfig(domain="switch")),
}
USER_SCHEMA = vol.Schema({vol.Required(CONF_NAME, default=NAME): TextSelector(), **OUTPUT_SCHEMA})
SETTINGS_SCHEMA = vol.Schema(OUTPUT_SCHEMA)

# The sources list shows each row's type as typed here, so values are the display labels.
TYPE_LABELS = {
    SOURCE_CALENDAR: "Calendar",
    SOURCE_CONDITION: "Condition",
    SOURCE_TEMPLATE: "Template",
}
LABEL_TYPES = {label: source_type for source_type, label in TYPE_LABELS.items()}

# One sortable list: drag to reorder, pencil to edit, Add for a new source. The edit dialog
# has the same fields for every type; the flow checks the ones each type needs.
# Home Assistant 2026.1 requires serialized selectors in the nested fields.
SOURCES_SELECTOR = ObjectSelector(
    ObjectSelectorConfig(
        multiple=True,
        label_field=CONF_NAME,
        description_field=CONF_TYPE,
        translation_key="source",
        fields={
            CONF_NAME: {
                "selector": TextSelector().serialize()["selector"],
                "required": True,
                "label": "Name",
            },
            CONF_TYPE: {
                "selector": SelectSelector(
                    SelectSelectorConfig(
                        options=list(TYPE_LABELS.values()), mode=SelectSelectorMode.DROPDOWN
                    )
                ).serialize()["selector"],
                "required": True,
                "label": "Type",
            },
            CONF_ENTITY: {"selector": EntitySelector().serialize()["selector"], "label": "Entity"},
            CONF_THEME: {"selector": TextSelector().serialize()["selector"], "label": "Theme"},
            CONF_TEMPLATE: {
                "selector": TemplateSelector().serialize()["selector"],
                "label": "Template",
            },
        },
    )
)

# Each end of a window is a clock time or a sun event; values are the display labels.
POINT_LABELS = {planner.TIME: "Time", planner.SUNRISE: "Sunrise", planner.SUNSET: "Sunset"}
LABEL_POINTS = {label: point for point, label in POINT_LABELS.items()}
_POINT_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=list(POINT_LABELS.values()), mode=SelectSelectorMode.DROPDOWN)
).serialize()["selector"]
_TIME_SELECTOR = TimeSelector().serialize()["selector"]
_OFFSET_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=MIN_WINDOW_OFFSET,
        max=MAX_WINDOW_OFFSET,
        step=1,
        unit_of_measurement="min",
        mode=NumberSelectorMode.BOX,
    )
).serialize()["selector"]

WINDOWS_SELECTOR = ObjectSelector(
    ObjectSelectorConfig(
        multiple=True,
        label_field=CONF_NAME,
        translation_key="window",
        fields={
            CONF_NAME: {"selector": TextSelector().serialize()["selector"], "label": "Name"},
            "start": {"selector": _POINT_SELECTOR, "required": True, "label": "Start"},
            "start_time": {"selector": _TIME_SELECTOR, "label": "Start time"},
            "start_offset": {"selector": _OFFSET_SELECTOR, "label": "Start offset"},
            "end": {"selector": _POINT_SELECTOR, "required": True, "label": "End"},
            "end_time": {"selector": _TIME_SELECTOR, "label": "End time"},
            "end_offset": {"selector": _OFFSET_SELECTOR, "label": "End offset"},
        },
    )
)


def settings(entry: ConfigEntry) -> dict[str, Any]:
    """Effective settings; options override data once Settings has been saved."""
    return {**entry.data, **entry.options}


def _output(user_input: dict[str, Any]) -> dict[str, Any]:
    """Settings to store. A cleared outlet is stored as None so it overrides the original data."""
    return {
        CONF_LIGHT: user_input[CONF_LIGHT],
        CONF_PRESET: user_input[CONF_PRESET],
        CONF_POWER: user_input.get(CONF_POWER) or None,
    }


def _output_in_use(
    entries: list[ConfigEntry], user_input: dict[str, Any], exclude: str | None = None
) -> bool:
    """Another setup already drives this light or preset select."""
    return any(
        entry.entry_id != exclude
        and (
            settings(entry).get(CONF_LIGHT) == user_input[CONF_LIGHT]
            or settings(entry).get(CONF_PRESET) == user_input[CONF_PRESET]
        )
        for entry in entries
    )


class HolidayLightingConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if _output_in_use(self._async_current_entries(include_ignore=False), user_input):
                return self.async_abort(reason="already_configured")
            if not (name := user_input[CONF_NAME].strip()):
                errors[CONF_NAME] = "name_required"
            else:
                return self.async_create_entry(
                    title=name,
                    data=_output(user_input),
                    options={
                        CONF_SOURCES: [],
                        CONF_WINDOWS: [dict(window) for window in DEFAULT_WINDOWS],
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HolidayLightingOptionsFlow:
        return HolidayLightingOptionsFlow()


class HolidayLightingOptionsFlow(OptionsFlow):
    """Windows, sources and settings on one form. The update listener reloads the setup."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        errors: dict[str, str] = {}
        placeholders = {"source": "", "window": ""}
        if user_input is not None:
            sources, error, name = _parse_sources(
                self.hass, user_input.get(CONF_SOURCES) or [], entry.options.get(CONF_SOURCES, [])
            )
            windows, window_error, window_name = _parse_windows(user_input.get(CONF_WINDOWS) or [])
            output = _output(user_input)
            entries = self.hass.config_entries.async_entries(DOMAIN, include_ignore=False)
            if error:
                errors[CONF_SOURCES] = error
                placeholders["source"] = name
            elif window_error:
                errors[CONF_WINDOWS] = window_error
                placeholders["window"] = window_name
            elif _output_in_use(entries, output, exclude=entry.entry_id):
                errors["base"] = "output_in_use"
            else:
                # Settings go to options only once changed, so an unchanged form doesn't reload.
                changed = {
                    key: value
                    for key, value in output.items()
                    if key in entry.options or entry.data.get(key) != value
                }
                return self.async_create_entry(
                    data={**changed, CONF_SOURCES: sources, CONF_WINDOWS: windows}
                )
            current = user_input
        else:
            current = {
                k: v for k, v in settings(entry).items() if k in SETTINGS and v is not None
            } | {
                CONF_SOURCES: [_to_item(s) for s in entry.options.get(CONF_SOURCES, [])],
                CONF_WINDOWS: [
                    _window_item(w) for w in entry.options.get(CONF_WINDOWS, DEFAULT_WINDOWS)
                ],
            }
        schema = SETTINGS_SCHEMA.extend(
            {
                vol.Optional(CONF_WINDOWS): WINDOWS_SELECTOR,
                vol.Optional(CONF_SOURCES): SOURCES_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            errors=errors,
            description_placeholders=placeholders,
        )


def _to_item(source: dict[str, Any]) -> dict[str, Any]:
    """A stored source as a row of the sources list."""
    item = {
        CONF_NAME: source[CONF_NAME],
        CONF_TYPE: TYPE_LABELS[source[CONF_TYPE]],
    }
    if entity := source.get(CONF_ENTITY):
        item[CONF_ENTITY] = entity
    for key in (CONF_THEME, CONF_TEMPLATE):
        if source.get(key):
            item[key] = source[key]
    return item


def _parse_sources(
    hass: HomeAssistant, items: list[dict[str, Any]], existing: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], str | None, str]:
    """Rows of the sources list as stored sources, or an error key and the source's name."""
    sources: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in items:
        name = str(item.get(CONF_NAME) or "").strip()
        if not name:
            return [], "source_name_required", ""
        if name.lower() in names:
            return [], "source_name_in_use", name
        names.add(name.lower())
        source_type = LABEL_TYPES.get(item.get(CONF_TYPE), SOURCE_CALENDAR)
        source: dict[str, Any] = {CONF_NAME: name, CONF_TYPE: source_type}
        entity = str(item.get(CONF_ENTITY) or "").strip()
        if source_type == SOURCE_CALENDAR:
            if not entity.startswith("calendar."):
                return [], "source_calendar_required", name
            source[CONF_ENTITY] = entity
        elif source_type == SOURCE_CONDITION:
            theme = str(item.get(CONF_THEME) or "").strip()
            if not entity or not theme:
                return [], "source_condition_required", name
            source |= {CONF_ENTITY: entity, CONF_THEME: theme}
        else:
            text = str(item.get(CONF_TEMPLATE) or "")
            if not text.strip():
                return [], "source_template_required", name
            try:
                Template(text, hass).ensure_valid()
            except TemplateError:
                return [], "source_template_invalid", name
            source[CONF_TEMPLATE] = text
        sources.append(source)
    return _keep_ids(sources, existing), None, ""


def _keep_ids(
    sources: list[dict[str, Any]], existing: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Give each source the ID of the stored source it was edited from, so it keeps its switch.

    The list rows can't carry IDs, so a row matches a stored source with the same name, or
    else one with the same settings (a rename). Anything else is a new source.
    """

    def settings_of(source: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in source.items() if k not in (CONF_ID, CONF_NAME)}

    unclaimed = list(existing)
    ids: list[str | None] = [None] * len(sources)
    for match in (
        lambda new, old: new[CONF_NAME].lower() == old[CONF_NAME].lower(),
        lambda new, old: settings_of(new) == settings_of(old),
    ):
        for index, source in enumerate(sources):
            if ids[index] is None and (
                old := next((o for o in unclaimed if match(source, o)), None)
            ):
                ids[index] = old[CONF_ID]
                unclaimed.remove(old)
    return [
        {CONF_ID: source_id or ulid_now(), **source}
        for source_id, source in zip(ids, sources, strict=True)
    ]


def _window_item(window: dict[str, Any]) -> dict[str, Any]:
    """A stored lighting window as a row of the windows list."""
    item: dict[str, Any] = {CONF_NAME: window[CONF_NAME]}
    for end in (planner.START, planner.END):
        point = window[end]
        item[end] = POINT_LABELS[point]
        if point == planner.TIME:
            item[f"{end}_time"] = window[f"{end}_time"]
        else:
            item[f"{end}_offset"] = window.get(f"{end}_offset", 0)
    return item


def _parse_windows(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None, str]:
    """Rows of the windows list as stored windows, or an error key and the window's name.

    Each end keeps only the field its type uses. Windows that depend on the sun are checked
    each day instead, because sunrise and sunset move.
    """
    if not items:
        return [], "window_required", ""
    windows: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        name = str(item.get(CONF_NAME) or "").strip() or f"Window {index}"
        window: dict[str, Any] = {CONF_NAME: name}
        for end in (planner.START, planner.END):
            point = LABEL_POINTS.get(item.get(end))
            if point is None:
                return [], "window_point_required", name
            window[end] = point
            if point == planner.TIME:
                clock = planner.parse_time(item.get(f"{end}_time"))
                if clock is None:
                    return [], "window_time_required", name
                window[f"{end}_time"] = clock.isoformat()
                continue
            try:
                offset = round(float(item.get(f"{end}_offset") or 0))
            except (TypeError, ValueError):
                return [], "window_offset_invalid", name
            if not MIN_WINDOW_OFFSET <= offset <= MAX_WINDOW_OFFSET:
                return [], "window_offset_invalid", name
            window[f"{end}_offset"] = offset
        if window[planner.START] == window[planner.END] == planner.TIME:
            start = planner.parse_time(window["start_time"])
            stop = planner.parse_time(window["end_time"])
            # An end of 00:00 is midnight at the end of the day.
            if stop != time(0) and stop <= start:
                return [], "window_backwards", name
        windows.append(window)
    return windows, None, ""
