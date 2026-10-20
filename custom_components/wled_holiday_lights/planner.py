"""Pure planning rules for WLED Holiday Lights.

This module deliberately has no Home Assistant imports so the rules can be
unit tested with the standard library alone.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo
import re
from typing import Any

OFF = "Off"
# Theme names that can't have presets.
RESERVED = ("auto", "off")

# Each end of a lighting window is a clock time, or sunrise or sunset plus an offset.
START = "start"
END = "end"
TIME = "time"
SUNRISE = "sunrise"
SUNSET = "sunset"

_PRESET = re.compile(r"^\[([^\[\]]+)\]\s+(.+)$")


def parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO timestamp; naive or invalid values return None for aware use."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_time(value: Any) -> time | None:
    """A clock time such as ``18:30`` or ``18:30:00``, or None if invalid."""
    if not isinstance(value, str):
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def _moment(
    window: dict[str, Any],
    key: str,
    day: date,
    zone: tzinfo | None,
    sun: dict[str, datetime | None],
) -> datetime | None:
    """One end of a window on ``day``, or None if it can't be calculated."""
    kind = window.get(key)
    if kind == TIME:
        clock = parse_time(window.get(f"{key}_time"))
        if clock is None:
            return None
        # A clock end of 00:00 is the end of the day, so a window can run until midnight.
        if key == END and clock == time(0):
            day += timedelta(days=1)
        return datetime.combine(day, clock, zone)
    if kind in (SUNRISE, SUNSET) and (base := sun.get(kind)) is not None:
        return base + timedelta(minutes=window.get(f"{key}_offset") or 0)
    return None


def window_spans(
    windows: list[dict[str, Any]],
    day: date,
    zone: tzinfo | None,
    sun: dict[str, datetime | None],
) -> list[dict[str, Any]]:
    """When each lighting window is open on ``day``.

    A window looks like::

        {"name": str, "start": "time" | "sunrise" | "sunset", "start_time": "HH:MM:SS",
         "start_offset": minutes, "end": ..., "end_time": ..., "end_offset": ...}

    ``sun`` maps ``sunrise`` and ``sunset`` to that day's local times, or None when the
    event doesn't happen. Windows stay within the day: times outside it are clipped. A
    window stays dark (``start`` and ``end`` None) when a sun time it uses is missing or
    its end isn't after its start.
    """
    day_start = datetime.combine(day, time(0), zone)
    day_end = datetime.combine(day + timedelta(days=1), time(0), zone)
    spans = []
    for window in windows:
        start = _moment(window, START, day, zone, sun)
        end = _moment(window, END, day, zone, sun)
        if start is not None and end is not None:
            start, end = max(start, day_start), min(end, day_end)
            if end <= start:
                start = end = None
        else:
            start = end = None
        spans.append({"name": window.get("name") or "", "start": start, "end": end})
    return spans


def lighting_hours(now: datetime, spans: list[dict[str, Any]]) -> bool:
    """Lights may run while any window is open; overlapping windows simply combine."""
    return any(span["start"] is not None and span["start"] <= now < span["end"] for span in spans)


def next_hours_change(now: datetime, spans: list[dict[str, Any]]) -> datetime | None:
    """The next window opening or closing today, for an exact wake-up."""
    return min(
        (
            moment
            for span in spans
            for moment in (span["start"], span["end"])
            if moment is not None and moment > now
        ),
        default=None,
    )


def _unique_casefold(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        if value.lower() not in seen:
            seen.add(value.lower())
            result.append(value)
    return result


def parse_library(options: list[str] | None) -> dict[str, Any]:
    """Discover looks from preset names formatted as ``[Theme] Look name``."""
    looks: list[dict[str, str]] = []
    themes: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    names: list[str] = []
    cases: dict[str, str] = {}
    for option in options or []:
        match = _PRESET.match(option)
        if match and match[1].strip() and match[2].strip():
            theme = match[1].strip()
            if theme.lower() in RESERVED:
                warnings.append(
                    f"WLED preset '{option}' ignored: Auto and Off can't be theme names. Rename it."
                )
                continue
            if option in names:
                errors.append(f"Duplicate preset name: {option}")
            if theme.lower() in cases and cases[theme.lower()] != theme:
                errors.append(f"Theme capitalization conflict: {theme}")
            cases[theme.lower()] = theme
            names.append(option)
            looks.append({"theme": theme, "name": match[2].strip(), "option": option})
            themes.append(theme)
        elif option.startswith("["):
            warnings.append(f"WLED preset '{option}' ignored: rename it to '[Theme] Look name'.")
    return {
        "looks": looks,
        "themes": sorted(_unique_casefold(themes), key=str.lower),
        "warnings": warnings,
        "errors": errors,
    }


def _parse_event(event: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    title = str(event.get("summary") or "").strip()
    raw_start, raw_end = event.get("start"), event.get("end")
    all_day = (
        isinstance(raw_start, str)
        and isinstance(raw_end, str)
        and len(raw_start) == 10
        and len(raw_end) == 10
    )
    try:
        if all_day:
            start = datetime.combine(date.fromisoformat(raw_start), time(), now.tzinfo)
            end = datetime.combine(date.fromisoformat(raw_end), time(), now.tzinfo)
        else:
            start = parse_timestamp(raw_start)
            end = parse_timestamp(raw_end)
            if start is None or end is None or start.tzinfo is None or end.tzinfo is None:
                return None
            start, end = start.astimezone(now.tzinfo), end.astimezone(now.tzinfo)
    except (TypeError, ValueError):
        return None
    if not title or end <= start:
        return None
    if all_day:
        duration: float = (end.date() - start.date()).days * 86400
    else:
        duration = end.timestamp() - start.timestamp()
        if duration.is_integer():
            duration = int(duration)
    return {"theme": title, "duration": duration, "start": start, "end": end}


def next_boundary(events: list[dict[str, Any]] | None, now: datetime) -> datetime | None:
    """Earliest future start or end among valid events, for an exact wake-up."""
    boundaries = []
    for event in events or []:
        parsed = _parse_event(event, now)
        if parsed:
            boundaries += [moment for moment in (parsed["start"], parsed["end"]) if moment > now]
    return min(boundaries, default=None)


def calendar_requests(
    events: list[dict[str, Any]] | None, now: datetime
) -> tuple[list[dict[str, Any]], list[str]]:
    """Requests for the events active now, and warnings for invalid events.

    Ordered shortest event first, then later start, then title.
    """
    active = []
    warnings = []
    for event in events or []:
        parsed = _parse_event(event, now)
        if parsed is None:
            title = str(event.get("summary") or "").strip()
            warnings.append(f"Calendar event '{title}' ignored: its start or end time is invalid.")
        elif parsed["start"] <= now < parsed["end"]:
            active.append(parsed)
    active.sort(key=lambda r: (r["duration"], -r["start"].timestamp(), r["theme"]))
    requests = [
        {
            "theme": r["theme"],
            "detail": f"{r['start'].isoformat()} to {r['end'].isoformat()}",
            "start": r["start"].isoformat(),
            "end": r["end"].isoformat(),
        }
        for r in active
    ]
    return requests, warnings


def _pick_look(theme: str, library: dict[str, Any], now: datetime) -> str:
    looks = sorted(
        (item for item in library["looks"] if item["theme"] == theme),
        key=lambda item: (item["name"], item["option"]),
    )
    return looks[now.date().toordinal() % len(looks)]["option"] if looks else ""


def build_plan(
    *,
    now: datetime,
    library: dict[str, Any],
    enabled: bool,
    sources: list[dict[str, Any]],
    hours: bool,
    power_ok: bool,
    presets_loaded: bool = True,
) -> dict[str, Any]:
    """Decide the theme and look from an ordered list of evaluated sources.

    The first source in the list beats everything below it; within a source its
    requests keep the source's own order. The first request whose theme has a
    ``[Theme] Look name`` preset wins. A source result looks like::

        {"id": str, "name": str, "type": str, "enabled": bool, "available": bool,
         "requests": [{"theme": str, "detail": str, "start": str | None, "end": str | None}],
         "warnings": [str]}

    Sources with ``enabled`` False were not evaluated and only appear in ``sources``.
    ``power_ok`` is True when no outlet is configured.
    ``presets_loaded`` is False until WLED's preset list has been read at least once.
    """
    warnings = list(library["warnings"])
    errors = list(library["errors"])

    requests: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    for position, source in enumerate(sources, start=1):
        active = source.get("enabled", True)
        candidate_theme, candidate_look = None, ""
        if not active:
            candidate_reason = "Disabled"
        elif not source["available"]:
            candidate_reason = "Source unavailable"
        elif not source["requests"]:
            candidate_reason = "No active requests"
        else:
            candidate_reason = (
                "No matching preset" if presets_loaded else "Waiting for WLED presets"
            )
            for request in source["requests"]:
                if candidate_look := _pick_look(request["theme"], library, now):
                    candidate_theme = request["theme"]
                    candidate_reason = (
                        f"Would drive {candidate_theme} if this source had highest priority"
                    )
                    break
        statuses.append(
            {
                "id": source["id"],
                "name": source["name"],
                "type": source["type"],
                "position": position,
                "enabled": active,
                "available": source["available"] if active else None,
                "requests": len(source["requests"]) if active else 0,
                "warnings": list(source["warnings"]) if active else [],
                "theme": candidate_theme,
                "look": candidate_look,
                "reason": candidate_reason,
            }
        )
        if not active:
            continue
        warnings += [f"{source['name']}: {message}" for message in source["warnings"]]
        requests += [
            {
                "source": source["name"],
                "source_id": source["id"],
                "position": position,
                "theme": request["theme"],
                "detail": request.get("detail") or "",
                "start": request.get("start"),
                "end": request.get("end"),
                "status": "automation disabled" if not enabled else "lower priority",
            }
            for request in source["requests"]
        ]

    theme, look, reason, winner = OFF, "", "", None
    if not enabled:
        reason = "Automation disabled"
    else:
        for request in requests:
            if selected_look := _pick_look(request["theme"], library, now):
                theme = request["theme"]
                look = selected_look
                reason = f"{request['source']}: {theme}"
                winner = request["source"]
                request["status"] = "won"
                break
            if not presets_loaded:
                request["status"] = "presets not loaded"
                continue
            request["status"] = "no preset"
            if request["theme"].lower() in RESERVED:
                warnings.append(
                    f"{request['source']}: theme '{request['theme']}' is reserved and can't have "
                    "WLED presets. Use Mode Disabled or the source's Enabled switch instead."
                )
            else:
                warnings.append(
                    f"{request['source']}: no WLED preset for theme '{request['theme']}'. Save one "
                    f"named '[{request['theme']}] Look name', or change the source."
                )

    if enabled and requests and not look and not presets_loaded:
        warnings.append(
            "WLED presets not loaded yet. They load automatically when lighting hours start."
        )

    if not power_ok:
        errors.append("WLED power outlet unavailable.")

    if not requests:
        idle = "Nothing scheduled"
    elif not presets_loaded:
        idle = "Waiting for WLED presets"
    else:
        idle = "No WLED preset for the scheduled theme"
    return {
        "theme": theme,
        "look": look,
        "reason": reason or idle,
        "source": winner,
        "enabled": enabled,
        "hours": hours,
        "allowed": hours and look != "" and not errors,
        "requests": requests,
        "sources": statuses,
        "warnings": warnings,
        "errors": errors,
        "themes": library["themes"],
        "date": now.date().isoformat(),
        "evaluated": now.isoformat(),
    }
