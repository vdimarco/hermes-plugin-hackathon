"""Load a plugin directory as a package, the way Hermes does (hermes_plugins.<name>)."""

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"


def load(name):
    module_name = f"hermes_plugins_test.{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    if "hermes_plugins_test" not in sys.modules:
        pkg = types.ModuleType("hermes_plugins_test")
        pkg.__path__ = []
        sys.modules["hermes_plugins_test"] = pkg
    plugin_dir = PLUGINS / name
    spec = importlib.util.spec_from_file_location(
        module_name, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def submodule(name, sub):
    load(name)
    return sys.modules[f"hermes_plugins_test.{name}.{sub}"]


class FakeState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        import json

        json.dumps(value)  # the real PluginState rejects values that are not JSON
        self.data[key] = value


class FakeCtx:
    """Records what register() asks for. Mirrors the PluginContext methods the plugins use."""

    def __init__(self, config=None, dispatch=None):
        self.config = dict(config or {})
        self.state = FakeState()
        self.tools, self.hooks, self.commands, self.cli, self.providers = {}, {}, {}, {}, []
        self._dispatch = dispatch
        self.dispatched = []

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        assert schema["name"] == name
        self.tools[name] = handler

    def register_hook(self, name, callback, **kwargs):
        self.hooks.setdefault(name, []).append(callback)

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, **kwargs):
        self.cli[name] = (setup_fn, handler_fn)

    def register_delegation_provider(self, provider):
        self.providers.append(provider)

    def dispatch_tool(self, tool_name, args, **kwargs):
        self.dispatched.append((tool_name, dict(args), kwargs))
        if self._dispatch is None:
            raise RuntimeError("no dispatch configured")
        return self._dispatch(tool_name, args, **kwargs)
