import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from tests._load import PLUGINS, ROOT, FakeCtx, load, submodule

contract_pkg = load("contract")
model = submodule("contract", "model")
Ledger = submodule("contract", "ledger").Ledger
PLUGIN = PLUGINS / "contract"
SID = "s1"


def term_result(code, output=""):
    return json.dumps({"output": output, "exit_code": code, "error": None})


class FakeTerminal:
    """Plays the Hermes terminal tool for contract_run: a command -> (exit, output) table."""

    def __init__(self, table=None):
        self.table = dict(table or {})

    def __call__(self, tool, args, **kwargs):
        assert tool == "terminal"
        cmd = args["command"]
        if cmd.startswith("printf 'head="):
            return term_result(0, "head=abc123\ntree=deadbeef\ndirty=2\n")
        code, out = self.table.get(cmd, (127, f"{cmd}: not found"))
        return term_result(code, out)


class H:
    def __init__(self, config=None, table=None):
        self.term = FakeTerminal(table)
        self.ctx = FakeCtx(config, dispatch=self.term)
        self.p = contract_pkg.ContractPlugin(self.ctx, ledger=Ledger())

    def tool(self, tool, args, result="{}", status="ok"):
        self.p.on_post_tool_call(tool_name=tool, args=args, result=result, status=status, session_id=SID)

    def terminal(self, cmd, code=0, out=""):
        self.tool("terminal", {"command": cmd}, term_result(code, out), "ok" if code == 0 else "error")

    def propose(self, checks, **extra):
        return json.loads(self.p.tool_propose({"title": "Fix discount", "checks": checks, **extra}, session_id=SID))

    def contract(self):
        return self.p.get(SID)

    def pre(self, tool, args):
        return self.p.on_pre_tool_call(tool_name=tool, args=args, session_id=SID)


class ModelTests(unittest.TestCase):
    def test_new_contract_locks_named_files_and_tests(self):
        c = model.new_contract("t", ["pytest -q tests/test_cart.py::test_ten", {"command": "npm run lint"}])
        self.assertEqual([x["id"] for x in c["checks"]], ["c1", "c2"])
        self.assertIn("tests/test_cart.py", c["locked"])
        self.assertIn("conftest.py", c["locked"])
        self.assertEqual(c["status"], model.PROPOSED)
        self.assertEqual(c["hash"], model.contract_hash(c))

    def test_bad_contracts_are_refused(self):
        with self.assertRaises(model.ContractError):
            model.new_contract("t", [])
        with self.assertRaises(model.ContractError):
            model.new_contract("t", [{"command": ""}])
        with self.assertRaises(model.ContractError):
            model.new_contract("t", [{"command": "x", "expect_output": "("}])
        with self.assertRaises(model.ContractError):
            model.new_contract("t", ["true"] * 13)

    def test_path_locks(self):
        locks = ["tests/*", "*.spec.*", "src/contract.json"]
        self.assertEqual(model.path_locked("/home/u/app/tests/test_a.py", locks), "tests/*")
        self.assertEqual(model.path_locked("./web/a.spec.ts", locks), "*.spec.*")
        self.assertEqual(model.path_locked("src/contract.json", locks), "src/contract.json")
        self.assertIsNone(model.path_locked("src/cart.py", locks))

    def test_shell_write_targets(self):
        t = model.command_write_targets
        self.assertIn("tests/a.py", t("sed -i 's/90/81/' tests/a.py"))
        self.assertIn("tests/a.py", t("echo x > tests/a.py"))
        self.assertIn("tests/a.py", t("cat foo | tee tests/a.py"))
        self.assertIn("tests/a.py", t("rm -f tests/a.py && ls"))
        self.assertIn("tests/a.py", t("git checkout -- tests/a.py"))
        self.assertIn("tests/a.py", t("cp /tmp/x tests/a.py"))
        self.assertNotIn("tests/a.py", t("pytest -q tests/a.py > /tmp/out.txt"))
        self.assertNotIn("tests/a.py", t("cp tests/a.py /tmp/backup.py"))
        self.assertNotIn("tests/a.py", t("cat tests/a.py"))
        self.assertEqual(t("pytest -q 2>&1 | tail -5"), [])

    def test_lock_violation_by_tool(self):
        locks = ["tests/*"]
        self.assertIsNotNone(model.lock_violation("write_file", {"path": "tests/a.py"}, locks))
        self.assertIsNotNone(model.lock_violation("patch", {"mode": "patch", "patch": "*** Update File: tests/a.py\n"}, locks))
        self.assertIsNotNone(model.lock_violation("execute_code", {"code": "open('tests/a.py','w').write('')"}, locks))
        self.assertIsNone(model.lock_violation("execute_code", {"code": "print(open('tests/a.py').read())"}, locks))
        self.assertIsNone(model.lock_violation("read_file", {"path": "tests/a.py"}, locks))

    def test_check_matching(self):
        c = model.new_contract("t", ["pytest -q"])
        self.assertTrue(model.match_check(c, "pytest   -q"))
        self.assertTrue(model.match_check(c, "cd app && pytest -q"))
        self.assertTrue(model.match_check(c, "pytest -q | tail -3"))
        self.assertTrue(model.match_check(c, "pytest -q 2>&1"))
        self.assertFalse(model.match_check(c, "pytest -q tests/other.py"))
        self.assertFalse(model.match_check(c, "echo pytest -q"))

    def test_judge(self):
        check = {"id": "c1", "expect_exit": 0, "expect_output": r"200"}
        self.assertEqual(model.judge(check, 0, "HTTP 200 OK")[0], True)
        self.assertEqual(model.judge(check, 0, "HTTP 500")[0], False)
        self.assertEqual(model.judge({"id": "c1", "expect_exit": 0}, 0, "", failure_in_output=True)[0], False)
        self.assertEqual(model.judge({"id": "c1", "expect_exit": 1}, 1, "")[0], True)
        self.assertEqual(model.judge({"id": "c1", "expect_exit": 0, "forbid_output": "TODO"}, 0, "TODO: x")[0], False)


class FlowTests(unittest.TestCase):
    def test_proposed_contract_is_not_enforced_until_approved(self):
        h = H()
        out = h.propose(["pytest -q tests/test_cart.py"])
        self.assertEqual(out["status"], "proposed")
        self.assertIsNone(h.pre("write_file", {"path": "tests/test_cart.py"}))
        self.assertIn("approve", h.p.command("approve", session_id=SID))
        blocked = h.pre("write_file", {"path": "tests/test_cart.py"})
        self.assertEqual(blocked["action"], "block")
        self.assertIn("Contract lock", blocked["message"])
        self.assertIsNone(h.pre("write_file", {"path": "cart.py"}))

    def test_agent_cannot_replace_an_approved_contract(self):
        h = H()
        h.propose(["pytest -q"])
        h.p.command("approve", session_id=SID)
        out = h.propose(["true"])
        self.assertIn("error", out)
        self.assertEqual(h.contract()["checks"][0]["command"], "pytest -q")

    def test_gate_holds_until_checks_pass_then_writes_receipt(self):
        h = H()
        h.propose(["pytest -q"])
        h.p.command("approve", session_id=SID)
        h.tool("patch", {"path": "cart.py", "old_string": "a", "new_string": "b"})
        gate = h.p.on_pre_verify(session_id=SID, attempt=0, final_response="Done", changed_paths=["cart.py"])
        self.assertEqual(gate["action"], "continue")
        self.assertIn("has not run", gate["message"])
        h.terminal("pytest -q", 1, "F..\n1 failed, 2 passed")
        gate = h.p.on_pre_verify(session_id=SID, attempt=0, final_response="Done")
        self.assertIn("failed (exit 1", gate["message"])
        h.terminal("pytest -q | tail -1", 0, "1 failed, 2 passed")
        gate = h.p.on_pre_verify(session_id=SID, attempt=1, final_response="Done")
        self.assertIn("output shows failures", gate["message"])
        self.assertIsNone(h.p.on_pre_verify(session_id=SID, attempt=2, final_response="Done"))  # bounded
        h.terminal("pytest -q", 0, "3 passed")
        self.assertIsNone(h.p.on_pre_verify(session_id=SID, attempt=0, final_response="Done"))
        c = h.contract()
        self.assertEqual(c["status"], "met")
        ok, problems = model.verify_receipt(c["receipt"])
        self.assertTrue(ok, problems)
        self.assertEqual(c["receipt"]["changed_paths"], ["cart.py"])

    def test_edit_after_pass_makes_it_stale_and_reopens(self):
        h = H()
        h.propose(["pytest -q"])
        h.p.command("approve", session_id=SID)
        h.terminal("pytest -q", 0, "3 passed")
        h.p.on_pre_verify(session_id=SID, attempt=0, final_response="x")
        self.assertEqual(h.contract()["status"], "met")
        h.tool("write_file", {"path": "cart.py", "content": "x"})
        gate = h.p.on_pre_verify(session_id=SID, attempt=0, final_response="x")
        self.assertIn("passed before your last edit", gate["message"])
        self.assertEqual(h.contract()["status"], "active")
        self.assertTrue(h.contract()["receipt"]["stale"])

    def test_contract_run_uses_terminal_and_records_workspace(self):
        h = H(table={"pytest -q": (0, "3 passed"), "curl -s localhost:8000/health": (0, '{"ok": true}')})
        h.propose([{"command": "pytest -q"}, {"command": "curl -s localhost:8000/health", "expect_output": '"ok": true'}])
        h.p.command("approve", session_id=SID)
        out = json.loads(h.p.tool_run({}, session_id=SID, task_id="t1"))
        self.assertEqual(out["status"], "met", out)
        self.assertEqual(h.ctx.dispatched[0][2]["task_id"], "t1")
        receipt = h.contract()["receipt"]
        self.assertEqual(receipt["workspace"], {"head": "abc123", "tree": "deadbeef", "dirty": 2})
        self.assertEqual([r["source"] for r in receipt["results"]], ["contract_run", "contract_run"])

    def test_contract_run_reports_failures(self):
        h = H(table={"pytest -q": (1, "E   assert 81 == 90\n1 failed")})
        h.propose(["pytest -q"])
        h.p.command("approve", session_id=SID)
        out = json.loads(h.p.tool_run({}, session_id=SID))
        self.assertEqual(out["ran"][0]["passed"], False)
        self.assertIn("assert 81 == 90", out["ran"][0]["output_tail"])
        self.assertEqual(out["status"], "active")

    def test_contract_run_without_dispatch_explains_fallback(self):
        ctx = FakeCtx()
        p = contract_pkg.ContractPlugin(ctx, ledger=Ledger())
        p.tool_propose({"title": "t", "checks": ["pytest -q"]}, session_id=SID)
        out = json.loads(p.tool_run({}, session_id=SID))
        self.assertIn("terminal tool", out["error"])

    def test_proposed_contract_run_does_not_produce_receipt(self):
        h = H(table={"pytest -q": (0, "3 passed")})
        h.propose(["pytest -q"])
        out = json.loads(h.p.tool_run({}, session_id=SID))
        self.assertEqual(out["status"], "proposed")
        self.assertIsNone(h.contract()["receipt"])

    def test_auto_approve_when_configured(self):
        h = H({"require_approval": False})
        self.assertEqual(h.propose(["pytest -q"])["status"], "active")

    def test_context_and_footer(self):
        h = H()
        h.propose(["pytest -q"])
        ctx = h.p.on_pre_llm_call(session_id=SID, user_message="go")
        self.assertIn("Definition of done", ctx["context"])
        self.assertIn("/contract approve", h.p.on_transform_llm_output(response_text="Plan ready.", session_id=SID))
        h.p.command("approve", session_id=SID)
        self.assertIn("0/1 checks pass", h.p.on_transform_llm_output(response_text="Working.", session_id=SID))
        h.terminal("pytest -q", 0, "1 passed")
        met = h.p.on_transform_llm_output(response_text="Done.", session_id=SID)
        self.assertIn("Contract met", met)
        self.assertIsNone(h.p.on_transform_llm_output(response_text="Anything else?", session_id=SID))
        self.assertIsNone(h.p.on_pre_llm_call(session_id=SID))

    def test_user_commands(self):
        h = H()
        self.assertIn("No contract", h.p.command("", session_id=SID))
        self.assertIn("Check added", h.p.command("add pytest -q", session_id=SID))
        self.assertEqual(h.contract()["status"], "active")  # the user is the author
        self.assertEqual(h.contract()["approved_by"], "user")
        h.p.command("lock docs/spec.md", session_id=SID)
        self.assertIsNotNone(h.pre("write_file", {"path": "docs/spec.md"}))
        h.p.command("unlock docs/spec.md", session_id=SID)
        self.assertIsNone(h.pre("write_file", {"path": "docs/spec.md"}))
        self.assertIn("No receipt", h.p.command("receipt", session_id=SID))
        h.terminal("pytest -q", 0, "1 passed")
        h.p.on_pre_verify(session_id=SID, attempt=0, final_response="x")
        md = h.p.command("receipt", session_id=SID)
        self.assertIn("### ✅ Contract met", md)
        self.assertIn("```json", md)
        self.assertIn("dropped", h.p.command("reset", session_id=SID))
        self.assertIsNone(h.contract())

    def test_state_survives_restart(self):
        h = H()
        h.propose(["pytest -q"])
        h.p.command("approve", session_id=SID)
        again = contract_pkg.ContractPlugin(h.ctx, ledger=Ledger())
        self.assertEqual(again.get(SID)["status"], "active")
        self.assertIsNotNone(again.on_pre_tool_call(tool_name="write_file", args={"path": "tests/x.py"}, session_id=SID))

    def test_hooks_never_raise(self):
        h = H()
        h.p.on_post_tool_call(tool_name="terminal", args=None, result=object(), session_id=SID)
        self.assertIsNone(h.p.on_pre_tool_call(tool_name="terminal", args=None, session_id=SID))
        self.assertIsNone(h.p.on_pre_verify(session_id=SID))
        self.assertIsNone(h.p.on_transform_llm_output(response_text=None, session_id=SID))


class ReceiptTests(unittest.TestCase):
    def _receipt(self):
        h = H(table={"pytest -q": (0, "3 passed")})
        h.propose(["pytest -q"])
        h.p.command("approve", session_id=SID)
        h.p.tool_run({}, session_id=SID)
        return h, h.contract()["receipt"]

    def test_tampering_is_detected(self):
        _, receipt = self._receipt()
        self.assertTrue(model.verify_receipt(receipt)[0])
        for mutate in (
            lambda r: r["results"][0].update(exit_code=1),
            lambda r: r["contract"]["checks"][0].update(command="true"),
            lambda r: r.update(changed_paths=["other.py"]),
            lambda r: r["chain"].reverse() or r["chain"].append("0" * 64),
        ):
            bad = json.loads(json.dumps(receipt))
            mutate(bad)
            self.assertFalse(model.verify_receipt(bad)[0])

    def test_rerun_script(self):
        _, receipt = self._receipt()
        script = model.rerun_script(receipt)
        self.assertIn("(pytest -q); code=$?", script)
        self.assertTrue(script.startswith("#!/usr/bin/env bash"))

    def test_cli(self):
        h, receipt = self._receipt()
        parser = argparse.ArgumentParser()
        contract_pkg._cli_setup(parser)
        handler = contract_pkg._make_cli_handler(h.ctx)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "receipt.json")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(handler(parser.parse_args(["export", receipt["receipt_hash"][:8], "--out", path])), 0)
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(handler(parser.parse_args(["verify", path])), 0)
            self.assertIn("OK", buf.getvalue())
            self.assertIn("deadbeef", buf.getvalue())
            data = json.loads(Path(path).read_text())
            data["results"][0]["exit_code"] = 3
            Path(path).write_text(json.dumps(data))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(handler(parser.parse_args(["verify", path])), 2)
        buf = io.StringIO()
        with redirect_stdout(buf):
            handler(parser.parse_args(["list"]))
        self.assertIn("Fix discount", buf.getvalue())


class ManifestTests(unittest.TestCase):
    def test_register_matches_manifest(self):
        manifest = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
        ctx = FakeCtx()
        contract_pkg.register(ctx)
        self.assertEqual(sorted(ctx.tools), sorted(manifest["provides_tools"]))
        self.assertEqual(sorted(ctx.hooks), sorted(manifest["provides_hooks"]))
        self.assertEqual(manifest["version"], contract_pkg.VERSION)

    def test_catalog_entry_matches_manifest(self):
        entry = yaml.safe_load((ROOT / "catalog" / "contract.yaml").read_text())
        manifest = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
        self.assertEqual(entry["subdir"], "plugins/contract")
        for field in ("name", "version", "requires_hermes"):
            self.assertEqual(entry[field], manifest[field])
        self.assertEqual(sorted(entry["capabilities"]["provides_tools"]), sorted(manifest["provides_tools"]))
        self.assertEqual(sorted(entry["capabilities"]["provides_hooks"]), sorted(manifest["provides_hooks"]))

    def test_ledger_copy_matches_vouch(self):
        self.assertEqual((PLUGIN / "ledger.py").read_text(), (PLUGINS / "vouch" / "ledger.py").read_text())


if __name__ == "__main__":
    unittest.main()
