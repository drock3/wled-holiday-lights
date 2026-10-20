"""Constants for WLED Holiday Lights."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "wled_holiday_lights"
NAME: Final = "WLED Holiday Lights"

CONF_NAME: Final = "name"
CONF_LIGHT: Final = "light"
CONF_PRESET: Final = "preset"
CONF_POWER: Final = "power"
SETTINGS: Final = (CONF_LIGHT, CONF_PRESET, CONF_POWER)

# Ordered list of sources in the entry options; the first source wins.
CONF_SOURCES: Final = "sources"
CONF_ID: Final = "id"
CONF_TYPE: Final = "type"
CONF_ENTITY: Final = "entity"
CONF_THEME: Final = "theme"
CONF_TEMPLATE: Final = "template"
SOURCE_CALENDAR: Final = "calendar"
SOURCE_CONDITION: Final = "condition"
SOURCE_TEMPLATE: Final = "template"

# Lighting windows in the entry options; the lights may run while any window is open.
# The window format is documented in planner.window_spans.
CONF_WINDOWS: Final = "windows"
DEFAULT_WINDOWS: Final = (
    {
        "name": "Evening",
        "start": "sunset",
        "start_offset": 0,
        "end": "time",
        "end_time": "00:00:00",
    },
)
MIN_WINDOW_OFFSET: Final = -240
MAX_WINDOW_OFFSET: Final = 240
DEFAULT_BRIGHTNESS_PCT: Final = 50

MODE_AUTOMATIC: Final = "Automatic"
MODE_MANUAL: Final = "Manual"
MODE_DISABLED: Final = "Disabled"
MODES: Final = [MODE_AUTOMATIC, MODE_MANUAL, MODE_DISABLED]

STORAGE_VERSION: Final = 1

CALENDAR_REFRESH_INTERVAL: Final = timedelta(minutes=5)
RETRY_INTERVAL: Final = timedelta(minutes=5)
STARTUP_TIMEOUT: Final = 120.0
STARTUP_POLL: Final = 5.0
RELOAD_INTERVAL: Final = 30.0
CONFIRM_TIMEOUT: Final = 10.0
SERVICE_TIMEOUT: Final = 30.0
CALENDAR_TIMEOUT: Final = 30.0

# Repairs issue IDs are suffixed with the config entry ID; these are also the translation keys.
ISSUE_CONFIGURATION: Final = "configuration"
ISSUE_OUTPUT: Final = "output"

SERVICE_RECONCILE: Final = "reconcile"
