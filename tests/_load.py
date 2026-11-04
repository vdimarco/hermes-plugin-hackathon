"""Load the plugin directory as a package named ``vouch``, the way Hermes does."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    if "vouch" in sys.modules:
        return sys.modules["vouch"]
    spec = importlib.util.spec_from_file_location(
        "vouch", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["vouch"] = module
    spec.loader.exec_module(module)
    return module


class FakeState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakeCtx:
    """Records what register() asks for. Mirrors the PluginContext methods Vouch uses."""

    def __init__(self, config=None):
        self.config = dict(config or {})
        self.state = FakeState()
        self.tools, self.hooks, self.commands, self.cli = {}, {}, {}, {}

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
