"""Run the plugins' real shell commands against a real git repository.

The terminal tool is played by a small runner that executes the command with
bash in the requested workdir, the way Hermes' local backend does.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests._load import FakeCtx, load, submodule

HAVE_GIT = shutil.which("git") is not None and shutil.which("bash") is not None
mv = load("multiverse")
runs = submodule("multiverse", "runs")
contract_pkg = load("contract")
Ledger = submodule("contract", "ledger").Ledger

CART = '''def apply_discount(total, percent):
    discounted = total * (1 - percent / 100)
    discounted = discounted * (1 - percent / 100)
    return round(discounted, 2)
'''
CHECK = 'python3 -c "import cart; assert cart.apply_discount(100, 10) == 90, cart.apply_discount(100, 10)"'


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def make_repo(tmp):
    repo = Path(tmp) / "shop"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    (repo / "cart.py").write_text(CART)
    (repo / ".gitignore").write_text("__pycache__/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo.resolve()


class Terminal:
    def __init__(self, cwd):
        self.cwd = str(cwd)

    def run(self, args):
        wd = args.get("workdir") or self.cwd
        p = subprocess.run(["bash", "-c", args["command"]], cwd=wd, capture_output=True, text=True, timeout=60)
        return json.dumps({"output": (p.stdout + p.stderr)[-8000:], "exit_code": p.returncode, "error": None})


@unittest.skipUnless(HAVE_GIT, "needs git and bash")
class MultiverseRealGit(unittest.TestCase):
    def test_race_adopt_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            term = Terminal(repo)
            fixes = {
                0: CART.replace("    discounted = discounted * (1 - percent / 100)\n", "    discounted = discounted * 1.0\n"),
                1: CART.replace("    discounted = discounted * (1 - percent / 100)\n", ""),
                2: CART.replace("discounted * (1 - percent / 100)", "discounted * (1 - percent / 200)"),
            }
            ref = []

            def dispatch(tool, args, **kw):
                if tool == "terminal":
                    return term.run(args)
                p = ref[0]
                for i, task in enumerate(args["tasks"]):
                    sid = f"child-{i}"
                    p.on_subagent_start(child_goal=task["goal"], child_session_id=sid)
                    d = p.on_pre_tool_call(tool_name="write_file", args={"path": "cart.py", "content": fixes[i]}, session_id=sid)
                    Path(d["args"]["path"]).write_text(fixes[i])  # what write_file would do with the pinned path
                    d = p.on_pre_tool_call(tool_name="terminal", args={"command": CHECK}, session_id=sid)
                    json.loads(term.run({"command": CHECK, **d["args"]}))
                    p.on_subagent_stop(child_session_id=sid, child_summary=f"attempt {i}", child_status="completed")
                return json.dumps({"results": []})

            ctx = FakeCtx(dispatch=dispatch)
            plugin = mv.Multiverse(ctx)
            ref.append(plugin)
            data = json.loads(plugin.tool_race({"goal": "Fix the double discount", "checks": [CHECK]}))
            self.assertEqual(data.get("winner"), "u2", data)
            self.assertEqual(data["universes"]["u1"]["checks"], "1/1")  # u1 passes too, with a bigger diff
            self.assertEqual(data["universes"]["u3"]["checks"], "0/1")
            self.assertEqual((repo / "cart.py").read_text(), CART)  # the real tree is untouched before adopt
            run = plugin.get_run(data["run"])
            for u in run["universes"].values():
                self.assertTrue(Path(u["path"]).is_dir())
            adopted = json.loads(plugin.tool_adopt({}))
            self.assertEqual(adopted.get("adopted"), "u2", adopted)
            self.assertEqual((repo / "cart.py").read_text(), fixes[1])
            for u in run["universes"].values():
                self.assertFalse(Path(u["path"]).exists())
            listed = subprocess.run(["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True).stdout
            self.assertEqual(len(listed.strip().splitlines()), 1)

    def test_uncommitted_changes_are_the_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            (repo / "cart.py").write_text(CART + "\n# local edit\n")
            term = Terminal(repo)
            plugin = mv.Multiverse(FakeCtx(dispatch=lambda tool, args, **kw: term.run(args)))
            data = json.loads(plugin.tool_open({"goal": "g", "checks": ["true"], "count": 2}))
            run = plugin.get_run(data["run"])
            self.assertEqual(run["base_kind"], "working tree")
            self.assertIn("# local edit", (Path(run["universes"]["u1"]["path"]) / "cart.py").read_text())
            json.loads(plugin.tool_close({}))
            self.assertFalse(Path(run["universes"]["u1"]["path"]).exists())


@unittest.skipUnless(HAVE_GIT, "needs git and bash")
class ContractRealGit(unittest.TestCase):
    def test_contract_run_and_receipt_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            term = Terminal(repo)
            ctx = FakeCtx(dispatch=lambda tool, args, **kw: term.run(args))
            p = contract_pkg.ContractPlugin(ctx, ledger=Ledger())
            p.tool_propose({"title": "Fix discount", "checks": [CHECK]}, session_id="s")
            p.command("approve", session_id="s")
            first = json.loads(p.tool_run({}, session_id="s"))
            self.assertEqual(first["ran"][0]["passed"], False)
            self.assertIn("81.0", first["ran"][0]["output_tail"])
            fixed = CART.replace("    discounted = discounted * (1 - percent / 100)\n", "")
            (repo / "cart.py").write_text(fixed)
            p.on_post_tool_call(tool_name="write_file", args={"path": "cart.py"}, result="{}", status="ok", session_id="s")
            done = json.loads(p.tool_run({}, session_id="s"))
            self.assertEqual(done["status"], "met", done)
            ws = p.get("s")["receipt"]["workspace"]
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "fix")
            tree = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], capture_output=True, text=True).stdout.strip()
            self.assertEqual(ws["tree"], tree)  # the receipt names exactly the tree that was committed
            self.assertEqual(ws["dirty"], 1)

    def test_rerun_script_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            term = Terminal(repo)
            ctx = FakeCtx(dispatch=lambda tool, args, **kw: term.run(args))
            p = contract_pkg.ContractPlugin(ctx, ledger=Ledger())
            p.tool_propose({"title": "t", "checks": ["test -f cart.py", CHECK]}, session_id="s")
            p.command("approve", session_id="s")
            (repo / "cart.py").write_text(CART.replace("    discounted = discounted * (1 - percent / 100)\n", ""))
            p.on_post_tool_call(tool_name="write_file", args={"path": "cart.py"}, result="{}", status="ok", session_id="s")
            self.assertEqual(json.loads(p.tool_run({}, session_id="s"))["status"], "met")
            script = submodule("contract", "model").rerun_script(p.get("s")["receipt"])
            r = subprocess.run(["bash", "-c", script], cwd=repo, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("PASS c2", r.stdout)
            (repo / "cart.py").write_text(CART)
            r = subprocess.run(["bash", "-c", script], cwd=repo, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("FAIL c2", r.stdout)


if __name__ == "__main__":
    unittest.main()
