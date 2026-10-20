<img src="custom_components/wled_holiday_lights/brand/icon.png" alt="WLED Holiday Lights" width="96">

# WLED Holiday Lights

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=drock3&repository=wled-holiday-lights&category=integration)

Automatically choose WLED presets for holidays and everyday occasions without
maintaining a separate automation for every theme. WLED Holiday Lights is a custom
Home Assistant integration that combines calendars, on/off conditions, and templates
into one ordered lighting schedule. Define your own themes and sources to fit your
home.

- Uses your existing Home Assistant WLED light and preset selector.
- The first enabled source with a matching theme wins.
- Runs during lighting windows you define, each starting and ending at a clock time or
  at sunrise/sunset with an offset (sunset until midnight by default).
- Rotates between a theme's looks daily.
- Provides brightness, mode, and source controls, plus schedule and output diagnostics.
- Optionally controls the outlet powering your WLED controller.
- Supports multiple lighting setups, each with its own light and preset selector.

## Requirements

- Home Assistant **2026.1.0 or newer**.
- A working [WLED integration](https://www.home-assistant.io/integrations/wled/)
  exposing a light entity and preset select entity.
- WLED presets named as described below.
- Correct Home Assistant location and time zone, so sunrise, sunset and midnight are calculated correctly.
- [HACS](https://www.hacs.xyz/docs/use/download/download/) for the recommended installation method.

Calendar sources additionally need a Home Assistant calendar that supports
`calendar.get_events`. No calendar is required for condition or template sources.

## Install with HACS

This repository can be installed as a **custom repository**; it does not need to
be listed in the default HACS catalog.

1. Open **HACS** in Home Assistant.
2. Open the menu in the top-right corner and select **Custom repositories**.
3. Enter `https://github.com/drock3/wled-holiday-lights` and select
   **Integration** as the type.
4. Add the repository, find **WLED Holiday Lights**, and download it.
5. **Restart Home Assistant.**
6. Go to **Settings > Devices & services > Add integration** and search for
   **WLED Holiday Lights**.

Alternatively, use the **Open in HACS** badge above to open the repository in your
Home Assistant instance.

### Manual installation

Copy the entire `custom_components/wled_holiday_lights` directory from this repository
into your Home Assistant configuration directory, keeping this layout:

```text
<config>/custom_components/wled_holiday_lights/manifest.json
<config>/custom_components/wled_holiday_lights/__init__.py
...
```

Restart Home Assistant, then add **WLED Holiday Lights** through **Devices & services**.
Setup is UI-only; do not add a `wled_holiday_lights:` section to your configuration YAML.

> [!IMPORTANT]
> Version **1.0.0** is the first supported release and requires a fresh setup.
> Alpha configurations and stored state are not migrated. If you used a test
> version, record your settings and remove its integration entries through
> **Devices & services** before installing this release. Remove any old
> `holiday_lighting` custom component directory as well. Restart Home Assistant,
> add **WLED Holiday Lights**, and recreate your sources. Update dashboards and
> automations to use the newly created entities.

### Updating

Download updates through HACS, then **restart Home Assistant** to load the new
version. For a manual installation, replace the integration directory with the
updated files and restart Home Assistant.

## Configure your lights

### 1. Name your WLED presets

Save presets in WLED using the format **`[Theme] Look name`**, for example:

```text
[Christmas] Candy cane
[Christmas] Warm sparkle
[Halloween] Purple and orange
[Birthday] Celebration
```

The theme must match the calendar title, condition theme, or template output,
including capitalization. Multiple presets for one theme are rotated daily.
`Auto` and `Off` are reserved and cannot be theme names. Presets without the naming
format are not used by the schedule.

### 2. Add a lighting setup

During setup, choose:

| Setting | Purpose |
| --- | --- |
| Name | Identifies this setup and its device/entities. |
| WLED light | The native WLED light to control. |
| WLED preset selector | The native WLED select entity whose options contain your presets. |
| Outlet switch | Optional switch powering the WLED controller. |

Each light and preset selector can belong to only one lighting setup.

> [!WARNING]
> When an outlet is configured, turning the lighting output off also switches off
> that outlet. Only select an outlet dedicated to the WLED controller.

### 3. Set lighting windows

Select **Configure** on the integration entry. The lights may run only while a
lighting window is open; a new setup starts with one window from sunset until
midnight. Add, edit, or remove windows to fit your schedule. Each end of a window is
one of:

| End type | Configuration |
| --- | --- |
| Time | A clock time, such as `06:00`. As an end time, `00:00` means midnight at the end of the day. |
| Sunrise | Sunrise plus an offset from -240 to +240 minutes; negative is before sunrise. |
| Sunset | Sunset plus an offset from -240 to +240 minutes; negative is before sunset. |

For example, a morning and an evening session:

| Window | Start | End |
| --- | --- | --- |
| Morning | Time `06:00` | Sunrise, offset `15` |
| Evening | Sunset, offset `-10` | Time `23:30` |

Windows are worked out each day and stay within that day, so times past midnight are
cut off at midnight. To run past midnight, add a second window starting at `00:00`;
the daily look rotation then moves to the next look at midnight. Overlapping windows
simply combine. On a day when a window's end isn't after its start, or a sun event it
uses doesn't happen at your location, that window stays closed. For lights at any
time of day, use a window from `00:00` to `00:00`.

The **In lighting hours** sensor shows today's opening and closing times for each
window and the next change.

### 4. Add sources in priority order

Add sources on the same **Configure** form and drag them into
priority order. The first enabled source with a theme that has a matching WLED
preset wins; an unmatched request does not block lower-priority sources.

| Source | Configuration | Theme request |
| --- | --- | --- |
| Calendar | Choose a calendar entity. | The title of an active event. Overlapping events are considered shortest first. |
| Condition | Choose an entity and enter a theme. | The configured theme while the entity's state is `on`. |
| Template | Enter a Home Assistant template. | The rendered theme name. |

Use source order to decide which occasions take precedence over your normal
schedule. Each source also has an enable/disable switch on the integration's
device.

A template can request a theme conditionally:

```jinja
{{ 'Party' if is_state('input_boolean.party_mode', 'on') else '' }}
```

Empty output, `none`, `false`, `unknown`, and `unavailable` request no theme.
Calendar events can be timed or all-day; their titles should contain only the
theme name, such as `Christmas`.

### Example: game-day lighting

Game days are one way to use the same source framework, not a built-in scheduling
mode. If you want team colors to take priority over a holiday:

1. Save a WLED preset named `[Game day] Team colors`.
2. Add a condition source using an on/off entity you control, such as
   `input_boolean.game_day`, and set its theme to `Game day`.
3. Place that source above your holiday calendar.

While the entity is `on`, the game-day theme wins during lighting hours. When it
turns `off`, the calendar can take over again. The same pattern works for birthdays,
parties, or any other occasion with a matching preset.

## Daily controls

| Control | Behavior |
| --- | --- |
| Mode: Automatic | Applies the winning theme during lighting hours; otherwise keeps the lights off. |
| Mode: Manual | Leaves WLED alone for direct control or editing; returns to Automatic at local midnight. |
| Mode: Disabled | Keeps the lights off until you change the mode. |
| Brightness | Target brightness, from 1 to 100 percent. |
| Reapply to WLED | Re-evaluates and resends power, preset, and brightness commands; unavailable in Manual mode. |

In Automatic mode, direct changes to WLED can be corrected at the next schedule
check. Switch to Manual before editing presets or controlling WLED yourself.

For automations, the `wled_holiday_lights.reconcile` action re-checks the schedule and
corrects drift. Select the setup's device or entity as a target when more than one
setup exists. This action is not available in Manual mode.

## Troubleshooting

- **Integration not found after download:** restart Home Assistant and confirm the
  files are directly inside `<config>/custom_components/wled_holiday_lights`, not an
  extra nested directory.
- **Nothing turns on:** check the mode, lighting windows (see the **In lighting
  hours** attributes), enabled sources, and exact
  theme spelling. There is no fallback theme when nothing matches.
- **Missing presets:** confirm the WLED preset selector exposes the expected
  names. With a switched outlet, presets may not load until lighting hours begin.
- **Unexpected theme:** inspect **Winning source**, **Schedule status**, and
  **Scheduled preset**, then check source order and overlapping calendar events.
- **Output failure:** inspect **Last output result**, **Output result detail**,
  diagnostic sensors, and **Settings > System > Repairs**. Confirm the WLED
  entities and optional outlet are available.

Report reproducible problems in the
[issue tracker](https://github.com/drock3/wled-holiday-lights/issues), including
your Home Assistant and integration versions and relevant logs. Remove personal
information before posting.
