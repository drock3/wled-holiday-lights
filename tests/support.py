"""Load the real integration with only its external HA interfaces stubbed."""

from contextlib import contextmanager
import importlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "wled_holiday_lights"
PACKAGE = "custom_components.wled_holiday_lights"


class ConfigEntry:
    def __class_getitem__(cls, item):
        return cls


class ConfigFlow:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()


class TemplateError(Exception):
    pass


class StubModule(ModuleType):
    def __getattr__(self, name):
        value = MagicMock(name=name)
        setattr(self, name, value)
        return value


@contextmanager
def integration():
    names = (
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.const",
        "homeassistant.core",
        "homeassistant.exceptions",
        "homeassistant.helpers",
        "homeassistant.helpers.config_validation",
        "homeassistant.helpers.entity_registry",
        "homeassistant.helpers.issue_registry",
        "homeassistant.helpers.service",
        "homeassistant.helpers.storage",
        "homeassistant.helpers.sun",
        "homeassistant.helpers.target",
        "homeassistant.helpers.typing",
        "homeassistant.helpers.dispatcher",
        "homeassistant.helpers.event",
        "homeassistant.helpers.start",
        "homeassistant.helpers.selector",
        "homeassistant.helpers.template",
        "homeassistant.util",
        "homeassistant.util.dt",
        "homeassistant.util.ulid",
        "voluptuous",
    )
    modules = {}
    for name in names:
        module = StubModule(name)
        modules[name] = module
        if "." in name:
            parent, attr = name.rsplit(".", 1)
            setattr(modules[parent], attr, module)
    modules["homeassistant.config_entries"].ConfigEntry = ConfigEntry
    modules["homeassistant.config_entries"].ConfigFlow = ConfigFlow
    modules["homeassistant.config_entries"].OptionsFlow = ConfigFlow
    modules["homeassistant.core"].callback = lambda func: func
    modules["homeassistant.exceptions"].TemplateError = TemplateError
    constants = modules["homeassistant.const"]
    for state in ("on", "off", "unknown", "unavailable"):
        setattr(constants, f"STATE_{state.upper()}", state)

    # Each test imports fresh modules so patches and globals cannot leak between tests.
    with patch.dict(sys.modules):
        for name in list(sys.modules):
            if name == PACKAGE or name.startswith(f"{PACKAGE}."):
                del sys.modules[name]
        sys.modules.update(modules)
        spec = importlib.util.spec_from_file_location(
            PACKAGE,
            COMPONENT / "__init__.py",
            submodule_search_locations=[str(COMPONENT)],
        )
        package = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE] = package
        spec.loader.exec_module(package)
        yield SimpleNamespace(
            setup=package,
            const=importlib.import_module(f"{PACKAGE}.const"),
            controller=importlib.import_module(f"{PACKAGE}.controller"),
            flow=importlib.import_module(f"{PACKAGE}.config_flow"),
            sources=importlib.import_module(f"{PACKAGE}.sources"),
        )
