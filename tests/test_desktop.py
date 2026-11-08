"""The desktop halves: events from the Python side, Hermes' desktop lint, and the Node tests."""

import json
import re
import shutil
import subprocess
import unittest

from tests._load import PLUGINS, ROOT, FakeCtx, load, submodule

PLUGIN_NAMES = ("contract", "multiverse")

# Copied from Hermes' admission lint (hermes_cli/plugin_validate_desktop.py, _FORBIDDEN), which the
# catalog CI runs as the "desktop surface" check. Kept here so a violation fails before submission.
FORBIDDEN = (
    ("dynamic code evaluation", r"(?<![\w$.])eval\(|\bnew\s+Function\("),
    ("dynamic import outside the SDK", r"\bimport\(\s*(?!['\"](?:@hermes/plugin-sdk|react)(?:/[\w/-]*)?['\"]\s*\))"),
    ("remote import outside the SDK", r"\bimport\s+(?:[^;'\"]*?\bfrom\s*)?['\"][a-zA-Z][\w+.-]*:"),
    ("script injection", r"createElement\(\s*['\"]script['\"]\s*\)|<script\b"),
    ("app DOM reach", r"\bdocument\.(?:querySelector(?:All)?|getElementsBy\w+|getElementById)\(\s*[`'\"][^`'\"]*\[data-(?:slot|tour|sidebar|testid)\b"),
    ("app DOM reach", r"\.observe\(\s*document\.body\s*,\s*\{[^}]*\b(?:childList|subtree)\b"),
    ("preview guest reach", r"\b(?:querySelector(?:All)?|closest|getElementsByTagName|createElement)\(\s*[`'\"]webview\b"),
    ("raw bridge (review cue)", r"\b(?:window|globalThis|self)\s*(?:\?\.|\.)\s*hermesDesktop\b"),
    ("app markup selector (review cue)", r"\[data-(?:slot|tour|sidebar|testid|tree-[\w-]+|pane-host|titlebar-cluster)\b"),
)
ALLOWED_IMPORTS = {"@hermes/plugin-sdk", "react", "react/jsx-runtime"}
HARDCODED_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, event, payload):
        json.dumps(payload)  # must be a JSON dict, as broadcast_plugin_event requires
        self.assertable = True
        self.events.append((event, payload))


def patch_emit(name):
    module = submodule(name, "desktop_events")
    recorder = Recorder()
    original = module.emit
    module.emit = recorder
    return recorder, lambda: setattr(module, "emit", original)


class DesktopSurfaceLint(unittest.TestCase):
    def test_each_plugin_has_a_desktop_half(self):
        for name in PLUGIN_NAMES:
            self.assertTrue((PLUGINS / name / "desktop" / "plugin.js").is_file(), name)

    def test_forbidden_constructs_and_imports(self):
        for name in PLUGIN_NAMES:
            source = (PLUGINS / name / "desktop" / "plugin.js").read_text()
            for rule, pattern in FORBIDDEN:
                self.assertIsNone(re.search(pattern, source), f"{name}: {rule}")
            imports = set(re.findall(r"^import\s[^'\"]*['\"]([^'\"]+)['\"]", source, re.MULTILINE))
            self.assertTrue(imports and imports <= ALLOWED_IMPORTS, f"{name}: {imports}")
            self.assertIsNone(HARDCODED_COLOR.search(source), f"{name}: use var(--ui-*) colours")
            self.assertRegex(source, rf"(?:id:\s*|const ID = )'{name}'", f"{name}: the desktop id must equal the plugin name")
            self.assertNotIn("setInterval(", source.replace("ctx.setInterval(", ""), f"{name}: untracked timer")


class EventTests(unittest.TestCase):
    def test_contract_publishes_state_and_blocks(self):
        rec, restore = patch_emit("contract")
        self.addCleanup(restore)
        pkg = load("contract")
        p = pkg.ContractPlugin(FakeCtx(), ledger=submodule("contract", "ledger").Ledger())
        p.tool_propose({"title": "Fix", "checks": ["pytest -q"]}, session_id="s")
        self.assertEqual(rec.events[-1][0], "state")
        self.assertEqual(rec.events[-1][1]["status"], "proposed")
        p.command("approve", session_id="s")
        p.on_post_tool_call(tool_name="terminal", args={"command": "pytest -q"}, session_id="s",
                            result=json.dumps({"output": "1 passed", "exit_code": 0}), status="ok")
        snap = rec.events[-1][1]
        self.assertEqual(snap["checks"][0]["state"], "pass")
        p.on_post_tool_call(tool_name="write_file", args={"path": "a.py"}, result="{}", status="ok", session_id="s")
        self.assertEqual(rec.events[-1][1]["checks"][0]["state"], "stale")  # the write made the pass stale
        p.on_pre_tool_call(tool_name="write_file", args={"path": "tests/test_a.py"}, session_id="s")
        event, payload = rec.events[-1]
        self.assertEqual((event, payload["path"], payload["lock"]), ("blocked", "tests/test_a.py", "tests/*"))
        p.command("reset", session_id="s")
        self.assertEqual(rec.events[-1][1]["status"], "none")

    def test_claim_check_publishes_reports(self):
        rec, restore = patch_emit("contract")
        self.addCleanup(restore)
        pkg = load("contract")
        p = pkg.ContractPlugin(FakeCtx(), ledger=submodule("contract", "ledger").Ledger())
        p.on_post_tool_call(tool_name="patch", args={"path": "a.py"}, result="{}", status="ok", session_id="s")
        p.on_pre_verify(session_id="s", attempt=0, final_response="All tests pass.")
        self.assertEqual(rec.events[-1][:1] + (rec.events[-1][1]["stage"],), ("report", "draft"))
        p.on_transform_llm_output(response_text="All tests pass.", session_id="s")
        event, payload = rec.events[-1]
        self.assertEqual((event, payload["stage"], payload["counts"]["unverified"]), ("report", "final", 1))
        self.assertEqual(payload["problems"][0]["label"], "tests pass")
        count = len(rec.events)
        p.on_transform_llm_output(response_text="Hello there.", session_id="s")  # no claims: nothing sent
        self.assertEqual(len(rec.events), count)

    def test_multiverse_publishes_runs_and_throttles_live_counts(self):
        rec, restore = patch_emit("multiverse")
        self.addCleanup(restore)
        tm = __import__("tests.multiverse.test_multiverse", fromlist=["make"])
        plugin, fake, _ = tm.make(passing=("u2",))
        data = json.loads(plugin.tool_race({"goal": "Fix it", "checks": ["pytest -q"]}, session_id="parent"))
        runs_sent = [p for e, p in rec.events if e == "run"]
        self.assertEqual(runs_sent[0]["status"], "open")
        self.assertEqual(runs_sent[0]["session"], "parent")
        self.assertEqual(runs_sent[-1]["status"], "judged")
        self.assertEqual(runs_sent[-1]["winner"], "u2")
        self.assertEqual(runs_sent[-1]["universes"][0]["label"], "u2")
        self.assertTrue(all(u["calls"] == 2 for u in runs_sent[-1]["universes"]))
        self.assertNotIn("summary", json.dumps(runs_sent[-1]))  # no subagent text goes to the UI
        # many pinned calls in a burst produce at most one throttled update
        run = plugin.get_run(data["run"])
        plugin.bindings["burst"] = (run["id"], "u1")
        run["status"] = "open"
        before = len(rec.events)
        for _ in range(25):
            plugin.on_pre_tool_call(tool_name="terminal", args={"command": "ls"}, session_id="burst")
        self.assertLessEqual(len(rec.events) - before, 1)

    def test_emit_is_safe_outside_hermes(self):
        for name in PLUGIN_NAMES:
            submodule(name, "desktop_events").emit("x", {"a": 1})  # no hermes_cli here: a quiet no-op


@unittest.skipUnless(shutil.which("node"), "needs node")
class NodeTests(unittest.TestCase):
    def test_desktop_halves_in_node(self):
        r = subprocess.run(["node", "--import", "./tests/desktop/register.mjs", "--test", "tests/desktop/test.mjs"],
                           cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
        self.assertIn("# fail 0", r.stdout)


if __name__ == "__main__":
    unittest.main()
