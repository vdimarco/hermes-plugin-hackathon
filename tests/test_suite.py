"""Rules every plugin in this repo must follow (Hermes catalog rules 9, 11, 13, 17)."""

import ast
import unittest

import yaml

from tests._load import PLUGINS, ROOT

BANNED_MODULES = {"subprocess", "socket", "urllib", "http", "requests", "httpx", "aiohttp", "ctypes", "multiprocessing"}
BANNED_NAMES = {"eval", "exec", "compile", "__import__", "setattr"}
BANNED_ATTRS = {"system", "popen", "spawn", "execv", "fork", "Thread"}


class SuiteRules(unittest.TestCase):
    def plugins(self):
        return sorted(p for p in PLUGINS.iterdir() if (p / "plugin.yaml").is_file())

    def test_three_plugins(self):
        self.assertEqual([p.name for p in self.plugins()], ["contract", "multiverse", "vouch"])

    def test_no_shell_network_or_core_overrides(self):
        for plugin in self.plugins():
            for path in plugin.glob("*.py"):
                tree = ast.parse(path.read_text())
                for node in ast.walk(tree):
                    where = f"{plugin.name}/{path.name}:{getattr(node, 'lineno', 0)}"
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertNotIn(alias.name.split(".")[0], BANNED_MODULES, where)
                    elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                        self.assertNotIn(node.module.split(".")[0], BANNED_MODULES, where)
                        self.assertFalse(node.module.startswith("hermes_cli"), where)
                    elif isinstance(node, ast.Call):
                        fn = node.func
                        if isinstance(fn, ast.Name):
                            self.assertNotIn(fn.id, BANNED_NAMES, where)
                        elif isinstance(fn, ast.Attribute):
                            self.assertNotIn(fn.attr, BANNED_ATTRS, where)
                    elif isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                                self.assertNotIn(target.value.id, {"sys", "os", "agent", "tools", "hermes_cli"}, where)

    def test_no_bytecode_or_binaries(self):
        for path in PLUGINS.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:  # caches are git-ignored
                self.assertNotIn(path.suffix, {".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".wasm"}, str(path))

    def test_catalog_entries(self):
        for plugin in self.plugins():
            entry = yaml.safe_load((ROOT / "catalog" / f"{plugin.name}.yaml").read_text())
            manifest = yaml.safe_load((plugin / "plugin.yaml").read_text())
            self.assertEqual(entry["subdir"], f"plugins/{plugin.name}")
            self.assertEqual(entry["repo"], manifest["homepage"])
            self.assertEqual(entry["tier"], "community")
            self.assertIn("Disclosure", entry["description"])
            self.assertTrue((plugin / "README.md").is_file(), f"{plugin.name} needs a README")
            self.assertRegex(manifest["requires_hermes"], r"^>=\d+\.\d+\.\d+$")
            self.assertRegex(entry["sha"], r"^[0-9a-f]{40}$")
            self.assertNotEqual(entry["sha"], "0" * 40, f"{plugin.name}: pin a real commit")
            self.assertEqual(entry["version"], manifest["version"])
            body = ROOT / "docs" / "catalog-prs" / f"{plugin.name}.md"
            self.assertTrue(body.is_file(), f"{plugin.name}: needs a catalog PR body")
            self.assertIn(entry["sha"], body.read_text())
            self.assertIn("Disclosure (rule 13)", body.read_text())


if __name__ == "__main__":
    unittest.main()
