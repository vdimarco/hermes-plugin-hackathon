import abc
import json
import sys
import types
import unittest

import yaml

from tests._load import PLUGINS, ROOT, FakeCtx, load, submodule

mv = load("multiverse")
runs = submodule("multiverse", "runs")
PLUGIN = PLUGINS / "multiverse"
ROOTDIR = "/work/shop"


def out(code, text=""):
    return json.dumps({"output": text, "exit_code": code, "error": None})


class FakeHermes:
    """Plays the terminal tool (git, checks) and delegate_task (runs fake subagents)."""

    def __init__(self, plugin_ref, passing=("u2",), diffs=None, wip="", delegate_ok=True, cwd=ROOTDIR):
        self.plugin_ref = plugin_ref
        self.passing = set(passing)
        self.diffs = diffs or {"u1": (1, 3, 1), "u2": (2, 10, 4), "u3": (0, 0, 0)}
        self.wip = wip
        self.delegate_ok = delegate_ok
        self.cwd = cwd
        self.commands = []
        self.child_calls = []

    def __call__(self, tool, args, **kwargs):
        if tool == "delegate_task":
            if not self.delegate_ok:
                return json.dumps({"error": "delegation disabled"})
            p = self.plugin_ref[0]
            for i, task in enumerate(args["tasks"]):
                sid = f"child-{i}"
                p.on_subagent_start(child_goal=task["goal"], child_session_id=sid, child_subagent_id=f"sa-{i}")
                # each child edits a file and runs a command; record what Multiverse turns them into
                self.child_calls.append(p.on_pre_tool_call(tool_name="write_file", args={"path": "cart.py", "content": "x"}, session_id=sid))
                self.child_calls.append(p.on_pre_tool_call(tool_name="terminal", args={"command": "pytest -q"}, session_id=sid))
                p.on_subagent_stop(child_session_id=sid, child_summary=f"summary {i}", child_status="completed", duration_ms=1000)
            return json.dumps({"results": [{"status": "completed"}]})
        assert tool == "terminal", tool
        cmd, wd = args["command"], args.get("workdir", "")
        self.commands.append((cmd, wd))
        if cmd == runs.INFO_CMD:
            return out(0, f"root={ROOTDIR}\ncwd={self.cwd}\nhead=1111111\nwip={self.wip}\n")
        if cmd.startswith("mkdir -p"):
            return out(0, "Preparing worktree (detached HEAD 1111111)")
        if "--shortstat" in cmd:
            label = cmd.split(".multiverse/")[1].split("/")[1].split()[0].strip("'")
            files, ins, dels = self.diffs.get(label, (0, 0, 0))
            stat = f" {files} files changed, {ins} insertions(+), {dels} deletions(-)" if files else ""
            return out(0, f"stat={stat}\n" + "".join(f"file=f{n}.py\n" for n in range(files)))
        if " apply " in cmd:
            return out(0, " M cart.py")
        if "worktree remove" in cmd:
            return out(0, "")
        if cmd == "pytest -q":
            label = wd.split("/")[-1] if wd else ""
            if any(wd.rstrip("/").split("/")[i] == lab for lab in self.passing for i in range(len(wd.split("/")))):
                return out(0, "3 passed")
            return out(1, f"1 failed ({label})")
        return out(127, "unknown")


def make(**kw):
    ref = []
    fake = FakeHermes(ref, **kw)
    ctx = FakeCtx(dispatch=fake)
    plugin = mv.Multiverse(ctx)
    ref.append(plugin)
    return plugin, fake, ctx


class PlanTests(unittest.TestCase):
    def test_plan_universes(self):
        us = runs.plan_universes(3)
        self.assertEqual([u["label"] for u in us], ["u1", "u2", "u3"])
        self.assertEqual(len({u["approach"] for u in us}), 3)
        us = runs.plan_universes([{"model": "anthropic/claude-x"}, {"model": "openai/gpt-y"}, {"label": "Bold Idea!"}])
        self.assertEqual([u["label"] for u in us], ["claude-x", "gpt-y", "bold-idea"])
        self.assertEqual(runs.plan_universes([{"label": "a"}, {"label": "a"}])[1]["label"], "a-2")
        for bad in (1, 7, [{}], "x"):
            with self.assertRaises(runs.RunError):
                runs.plan_universes(bad)

    def test_new_run_needs_goal_checks_and_git(self):
        us = runs.plan_universes(2)
        with self.assertRaises(runs.RunError):
            runs.new_run("", ["pytest"], us, ROOTDIR, ROOTDIR, "h", "")
        with self.assertRaises(runs.RunError):
            runs.new_run("g", [], us, ROOTDIR, ROOTDIR, "h", "")
        with self.assertRaises(runs.RunError):
            runs.new_run("g", ["pytest"], us, "", ROOTDIR, "h", "")
        run = runs.new_run("g", ["pytest -q"], us, ROOTDIR, ROOTDIR + "/api", "h", "w")
        self.assertEqual(run["base"], "w")
        self.assertEqual(run["cwd_rel"], "api")
        self.assertEqual(run["universes"]["u1"]["path"], f"/work/.shop.multiverse/{run['id']}/u1")
        self.assertEqual(runs.workdir(run, run["universes"]["u1"]), f"/work/.shop.multiverse/{run['id']}/u1/api")

    def test_marker_round_trip(self):
        self.assertEqual(runs.parse_marker("[multiverse:abcdef12/u2] fix it"), ("abcdef12", "u2"))
        self.assertIsNone(runs.parse_marker("fix it"))

    def test_parse_diff(self):
        d = runs.parse_diff("stat= 3 files changed, 12 insertions(+), 1 deletion(-)\nfile=a.py\nfile=b.py\n")
        self.assertEqual((d["files"], d["insertions"], d["deletions"], d["lines"]), (3, 12, 1, 13))
        self.assertEqual(d["names"], ["a.py", "b.py"])
        self.assertEqual(runs.parse_diff("stat=\n")["lines"], 0)


class PinningTests(unittest.TestCase):
    def setUp(self):
        self.run = runs.new_run("g", ["pytest -q"], runs.plan_universes(2), ROOTDIR, ROOTDIR, "h", "")
        self.u1, self.u2 = self.run["universes"]["u1"], self.run["universes"]["u2"]

    def test_terminal_is_pinned(self):
        r = runs.rewrite("terminal", {"command": "pytest -q"}, self.run, self.u1)
        self.assertEqual(r, {"action": "modify", "args": {"workdir": self.u1["path"]}})
        r = runs.rewrite("terminal", {"command": f"cd {ROOTDIR}/src && make", "workdir": ROOTDIR}, self.run, self.u1)
        self.assertEqual(r["args"]["workdir"], self.u1["path"])
        self.assertEqual(r["args"]["command"], f"cd {self.u1['path']}/src && make")
        r = runs.rewrite("terminal", {"command": f"cat {self.u2['path']}/cart.py"}, self.run, self.u1)
        self.assertEqual(r["action"], "block")

    def test_file_paths_are_pinned(self):
        r = runs.rewrite("write_file", {"path": "cart.py", "content": "x"}, self.run, self.u1)
        self.assertEqual(r["args"]["path"], f"{self.u1['path']}/cart.py")
        r = runs.rewrite("patch", {"path": f"{ROOTDIR}/src/a.py"}, self.run, self.u1)
        self.assertEqual(r["args"]["path"], f"{self.u1['path']}/src/a.py")
        self.assertIsNone(runs.rewrite("read_file", {"path": f"{self.u1['path']}/a.py"}, self.run, self.u1))
        self.assertIsNone(runs.rewrite("write_file", {"path": "/tmp/scratch.txt"}, self.run, self.u1))
        self.assertEqual(runs.rewrite("write_file", {"path": f"{self.u2['path']}/a.py"}, self.run, self.u1)["action"], "block")
        body = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** End Patch"
        r = runs.rewrite("patch", {"mode": "patch", "patch": body}, self.run, self.u1)
        self.assertIn(f"*** Update File: {self.u1['path']}/src/a.py", r["args"]["patch"])

    def test_nested_delegation_is_blocked(self):
        self.assertEqual(runs.rewrite("delegate_task", {}, self.run, self.u1)["action"], "block")

    def test_ranking_prefers_passing_then_small_real_diffs(self):
        for label, passed, lines in (("u1", True, 40), ("u2", True, 8)):
            u = self.run["universes"][label]
            u["results"] = [{"passed": passed}]
            u["diff"] = {"lines": lines, "files": 1, "insertions": lines, "deletions": 0}
        runs.rank(self.run)
        self.assertEqual(self.run["winner"], "u2")
        self.u2["diff"] = {"lines": 0, "files": 0}
        runs.rank(self.run)
        self.assertEqual(self.run["winner"], "u1")  # an empty diff never wins
        self.u1["results"] = [{"passed": False}]
        runs.rank(self.run)
        self.assertIsNone(self.run["winner"])


class RaceTests(unittest.TestCase):
    def test_full_race(self):
        plugin, fake, ctx = make(passing=("u2",))
        data = json.loads(plugin.tool_race({"goal": "Fix the discount bug", "checks": ["pytest -q"]},
                                           task_id="t1", session_id="parent"))
        self.assertEqual(data["winner"], "u2", data)
        self.assertIn("🏆", data["scoreboard"])
        self.assertEqual(data["universes"]["u1"]["checks"], "0/1")
        self.assertEqual(data["universes"]["u2"]["summary"], "summary 1")
        # every child's calls were pinned to its own worktree
        run = plugin.get_run(data["run"])
        paths = [c["args"]["path"] for c in fake.child_calls if c and "path" in c.get("args", {})]
        self.assertEqual(paths, [f"{u['path']}/cart.py" for u in run["universes"].values()])
        # the delegate_task call carried the run markers
        self.assertTrue(all(cmd for cmd, _ in fake.commands))
        adopted = json.loads(plugin.tool_adopt({}, task_id="t1"))
        self.assertEqual(adopted["adopted"], "u2")
        self.assertTrue(any(" apply " in c for c, _ in fake.commands))
        self.assertTrue(any("worktree remove" in c for c, _ in fake.commands))
        self.assertEqual(plugin.get_run(data["run"])["status"], "adopted")
        # after the run closes, a straggling child call is stopped
        self.assertEqual(plugin.on_pre_tool_call(tool_name="terminal", args={"command": "ls"}, session_id="child-0")["action"], "block")

    def test_race_falls_back_to_manual_delegation(self):
        plugin, fake, _ = make(delegate_ok=False)
        data = json.loads(plugin.tool_race({"goal": "g", "checks": ["pytest -q"], "count": 2}))
        self.assertIn("could not start the subagents", data["error"])
        self.assertEqual(len(data["delegate_task"]["tasks"]), 2)
        self.assertIn("[multiverse:", data["delegate_task"]["tasks"][0]["goal"])

    def test_open_then_judge_with_uncommitted_base(self):
        plugin, fake, _ = make(wip="2222222", cwd=ROOTDIR + "/api")
        data = json.loads(plugin.tool_open({"goal": "g", "checks": ["pytest -q"], "count": 3}))
        run = plugin.get_run(data["run"])
        self.assertEqual(run["base"], "2222222")
        self.assertIn("2222222", [c for c, _ in fake.commands if c.startswith("mkdir")][0])
        judged = json.loads(plugin.tool_judge({"run": run["id"]}))
        wds = {wd for c, wd in fake.commands if c == "pytest -q"}
        self.assertEqual(wds, {f"{u['path']}/api" for u in run["universes"].values()})
        self.assertEqual(judged["winner"], "u2")

    def test_no_winner_and_close(self):
        plugin, fake, _ = make(passing=())
        data = json.loads(plugin.tool_race({"goal": "g", "checks": ["pytest -q"]}))
        self.assertIsNone(data["winner"])
        self.assertIn("No clear winner", data["next"])
        self.assertIn("error", json.loads(plugin.tool_adopt({})))
        self.assertEqual(json.loads(plugin.tool_close({}))["closed"], data["run"])
        self.assertIn("No open", json.loads(plugin.tool_judge({}))["error"])

    def test_errors_are_json(self):
        plugin, _, _ = make()
        self.assertIn("error", json.loads(plugin.tool_race({"goal": "g", "checks": []})))
        self.assertIn("error", json.loads(plugin.tool_open({"goal": "", "checks": ["x"]})))

    def test_unbound_sessions_are_untouched(self):
        plugin, _, _ = make()
        self.assertIsNone(plugin.on_pre_tool_call(tool_name="terminal", args={"command": "ls"}, session_id="parent"))

    def test_slash_and_cli(self):
        plugin, _, ctx = make()
        self.assertIn("No multiverse runs", plugin.command(""))
        data = json.loads(plugin.tool_race({"goal": "Fix it", "checks": ["pytest -q"]}))
        self.assertIn(data["run"], plugin.command(""))
        self.assertIn("🏆", plugin.command(data["run"]))
        self.assertIn("clean up:", mv.format_runs_cli(ctx))


class ProviderTests(unittest.TestCase):
    def setUp(self):
        stub = types.ModuleType("agent.delegation_provider")

        class DelegationProvider(abc.ABC):
            @property
            @abc.abstractmethod
            def name(self): ...

            @abc.abstractmethod
            def route_task(self, task, config): ...

        stub.DelegationProvider = DelegationProvider
        self.saved = {k: sys.modules.get(k) for k in ("agent", "agent.delegation_provider")}
        sys.modules.setdefault("agent", types.ModuleType("agent"))
        sys.modules["agent.delegation_provider"] = stub

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def test_provider_routes_universe_models(self):
        plugin, _, ctx = make()
        mv.register(ctx)
        self.assertEqual(len(ctx.providers), 1)
        provider = mv._make_provider(plugin)
        self.assertEqual(provider.name, "multiverse")
        data = json.loads(plugin.tool_open({"goal": "g", "checks": ["pytest -q"], "attempts": [
            {"model": "anthropic/claude-x", "provider": "openrouter"}, {"approach": "small"}]}))
        tasks = data["delegate_task"]["tasks"]
        r1 = provider.route_task(tasks[0], {"model": "base-model", "service_tier": "fast"})
        self.assertEqual(r1["model"], "anthropic/claude-x")
        self.assertEqual(r1["provider"], "openrouter")
        r2 = provider.route_task(tasks[1], {"model": "base-model", "service_tier": "fast"})
        self.assertEqual(r2["model"], "base-model")
        self.assertNotIn("service_tier", r2)
        self.assertEqual(provider.route_task({"goal": "plain task"}, {"model": "m"}), {"model": "m"})


class ManifestTests(unittest.TestCase):
    def test_register_matches_manifest(self):
        manifest = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
        ctx = FakeCtx()
        mv.register(ctx)  # without Hermes the provider is skipped, the rest registers
        self.assertEqual(sorted(ctx.tools), sorted(manifest["provides_tools"]))
        self.assertEqual(sorted(ctx.hooks), sorted(manifest["provides_hooks"]))
        self.assertEqual(manifest["version"], mv.VERSION)

    def test_catalog_entry_matches_manifest(self):
        entry = yaml.safe_load((ROOT / "catalog" / "multiverse.yaml").read_text())
        manifest = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
        self.assertEqual(entry["subdir"], "plugins/multiverse")
        for field in ("name", "version", "requires_hermes"):
            self.assertEqual(entry[field], manifest[field])
        self.assertEqual(sorted(entry["capabilities"]["provides_tools"]), sorted(manifest["provides_tools"]))
        self.assertEqual(sorted(entry["capabilities"]["provides_hooks"]), sorted(manifest["provides_hooks"]))


if __name__ == "__main__":
    unittest.main()
