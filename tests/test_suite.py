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

    def test_two_plugins(self):
        # Vouch, the claim check, ships inside Contract since 1.2.0 (one catalog entry, one install).
        self.assertEqual([p.name for p in self.plugins()], ["contract", "multiverse"])

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
                        if node.module.startswith("hermes_cli"):
                            # the one Hermes module the docs name for pushing events to Desktop
                            self.assertEqual(node.module, "hermes_cli.plugin_events", where)
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


class CatalogImages(unittest.TestCase):
    """Catalog rule: `image` is an https image on a GitHub host, 2:1, pinned to the entry's sha."""

    def test_card_images(self):
        import struct

        for plugin in sorted(p for p in PLUGINS.iterdir() if (p / "plugin.yaml").is_file()):
            entry = yaml.safe_load((ROOT / "catalog" / f"{plugin.name}.yaml").read_text())
            url = entry.get("image", "")
            prefix = f"https://raw.githubusercontent.com/vdimarco/hermes-plugin-hackathon/{entry['sha']}/"
            self.assertTrue(url.startswith(prefix), f"{plugin.name}: image must be pinned to the entry sha")
            local = ROOT / url[len(prefix):]
            self.assertTrue(local.is_file(), f"{plugin.name}: {local} is missing")
            head = local.read_bytes()[:24]
            self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n", f"{plugin.name}: not a PNG")
            width, height = struct.unpack(">II", head[16:24])
            self.assertEqual(width, 2 * height, f"{plugin.name}: card must be 2:1, got {width}x{height}")
            self.assertLess(local.stat().st_size, 1_000_000, f"{plugin.name}: keep the card under 1 MB")


class InstallLinksAndRelease(unittest.TestCase):
    def test_desktop_install_links_point_at_real_plugins(self):
        import re
        from urllib.parse import parse_qs, urlparse

        links = set()
        for readme in [ROOT / "README.md"] + list(PLUGINS.glob("*/README.md")):
            links |= set(re.findall(r"hermes://plugin/install\?\S+", readme.read_text()))
        repo_links = [l for l in links if "repo=" in l]
        self.assertEqual(len(repo_links), 2, repo_links)
        for link in repo_links:
            params = parse_qs(urlparse(link).query)
            self.assertEqual(params.get("enable"), ["1"], link)
            url = params["repo"][0]
            m = re.fullmatch(r"https://github\.com/vdimarco/hermes-plugin-hackathon/tree/main/(plugins/[a-z]+)", url)
            self.assertTrue(m, url)
            self.assertTrue((ROOT / m.group(1) / "plugin.yaml").is_file(), url)
        catalog_links = sorted(l.rstrip("`).") for l in links if "catalog=" in l)
        self.assertEqual(catalog_links, [f"hermes://plugin/install?catalog={n}" for n in ("contract", "multiverse")])

    def test_release_notes_for_current_version(self):
        versions = {yaml.safe_load((p / "plugin.yaml").read_text())["version"] for p in PLUGINS.iterdir() if (p / "plugin.yaml").is_file()}
        self.assertEqual(len(versions), 1, "the plugins share one version")
        version = versions.pop()
        self.assertTrue((ROOT / "docs" / "releases" / f"v{version}.md").is_file())
        self.assertIn(f"## {version}", (ROOT / "CHANGELOG.md").read_text())
        workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
        self.assertIn("release", workflow["jobs"])
        self.assertEqual(workflow["permissions"], {"contents": "write"})


class PinnedCommit(unittest.TestCase):
    """The catalog sha must point at code with the same version (skipped when history is shallow)."""

    def test_pinned_code_matches_entry_version(self):
        import shutil
        import subprocess

        if not shutil.which("git"):
            self.skipTest("needs git")
        checked = 0
        for plugin in sorted(p for p in PLUGINS.iterdir() if (p / "plugin.yaml").is_file()):
            entry = yaml.safe_load((ROOT / "catalog" / f"{plugin.name}.yaml").read_text())
            r = subprocess.run(["git", "-C", str(ROOT), "show", f"{entry['sha']}:{entry['subdir']}/plugin.yaml"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                continue  # sha not in this (shallow) clone
            pinned = yaml.safe_load(r.stdout)
            self.assertEqual(str(pinned["version"]), str(entry["version"]), f"{plugin.name}: re-pin the catalog sha")
            if entry.get("image"):
                path = entry["image"].split(f"/{entry['sha']}/", 1)[1]
                exists = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{entry['sha']}:{path}"])
                self.assertEqual(exists.returncode, 0, f"{plugin.name}: {path} is not in the pinned commit")
            checked += 1
        if not checked:
            self.skipTest("pinned commit not in this clone")


if __name__ == "__main__":
    unittest.main()
