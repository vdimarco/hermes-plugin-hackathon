import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from tests._load import PLUGINS, ROOT, FakeCtx, load, submodule

contract = load("contract")
vouch = submodule("contract", "vouch")
checker = submodule("contract", "checker")
extract_claims = submodule("contract", "claims").extract_claims
Ledger = submodule("contract", "ledger").Ledger
PLUGIN = PLUGINS / "contract"

SID = "session-1"


def term(command, exit_code=0, output="", background=False):
    return ("terminal", {"command": command, "background": background},
            json.dumps({"output": output, "exit_code": exit_code, "error": None}), "ok" if exit_code in (0, None) else "error")


def write(path, ok=True):
    result = json.dumps({"success": True}) if ok else json.dumps({"error": "permission denied"})
    return ("write_file", {"path": path, "content": "x"}, result, "ok" if ok else "error")


def patch(path):
    return ("patch", {"path": path, "old_string": "a", "new_string": "b"}, json.dumps({"diff": "..."}), "ok")


class Harness:
    def __init__(self, config=None):
        self.ctx = FakeCtx(config)
        self.plugin = vouch.Vouch(self.ctx, ledger=Ledger())

    def run(self, *calls, sid=SID):
        for tool, args, result, status in calls:
            self.plugin.on_post_tool_call(tool_name=tool, args=args, result=result, status=status,
                                          session_id=sid, task_id="t", turn_id="turn")
        return self

    def report(self, reply, sid=SID):
        return checker.check_reply(reply, self.plugin.ledger.events(sid))

    def status_of(self, reply, kind=None):
        found = [f for f in self.report(reply).findings if kind is None or f.kind == kind]
        assert found, f"no claim found in {reply!r}"
        return found[0].status


class ClaimExtractionTests(unittest.TestCase):
    def kinds(self, text):
        return sorted({c.kind for c in extract_claims(text)})

    def test_finds_common_done_claims(self):
        self.assertEqual(self.kinds("All tests pass."), ["tests"])
        self.assertEqual(self.kinds("The build succeeds and lint is clean."), ["build", "lint"])
        self.assertEqual(self.kinds("I committed the changes and pushed to origin."), ["commit", "push"])
        self.assertEqual(self.kinds("Deployed to production."), ["deploy"])
        self.assertIn("edit", self.kinds("I updated `src/app.py` to handle None."))

    def test_skips_plans_questions_negations_and_instructions(self):
        for text in [
            "I did not run the tests.",
            "You can run `pytest` to check that the tests pass.",
            "Next, I will push the branch.",
            "Should I commit the changes?",
            "The tests don't pass yet.",
            "If you want, I can deploy it.",
            "Run `npm test` and make sure everything passes.",
        ]:
            self.assertEqual(extract_claims(text), [], text)

    def test_ignores_code_blocks_and_quotes(self):
        text = "Here is the log:\n```\n5 passed\nI committed it\n```\n> All tests pass\nDone."
        self.assertEqual(extract_claims(text), [])

    def test_marks_hedged_claims(self):
        claims = extract_claims("The tests should pass now.")
        self.assertEqual([c.kind for c in claims], ["tests"])
        self.assertTrue(claims[0].hedged)

    def test_edit_path_must_follow_the_verb(self):
        claims = extract_claims("I fixed the parser in src/parse.py. The config lives in config.yaml as before.")
        self.assertEqual([c.target for c in claims if c.kind == "edit"], ["src/parse.py"])

    def test_ran_claim_takes_the_command(self):
        claims = [c for c in extract_claims("I ran `make lint` and it is clean.") if c.kind == "ran"]
        self.assertEqual(claims[0].target, "make lint")


class VerdictTests(unittest.TestCase):
    def test_failed_test_run_contradicts_claim(self):
        h = Harness().run(patch("app.py"), term("pytest -q", 1, "F..\n1 failed, 2 passed in 0.1s"))
        report = h.report("Fixed the bug. All tests pass.")
        f = [f for f in report.findings if f.kind == "tests"][0]
        self.assertEqual(f.status, checker.CONTRADICTED)
        self.assertIn("exited 1", f.reason)
        self.assertIn("1 failed", f.reason)

    def test_pipe_that_hides_the_exit_code_is_caught(self):
        h = Harness().run(term("pytest -q | tail -3", 0, "FAILED tests/test_a.py::test_x\n1 failed, 4 passed"))
        f = h.report("All tests pass.").findings[0]
        self.assertEqual(f.status, checker.CONTRADICTED)
        self.assertIn("output shows failures", f.reason)

    def test_edit_after_passing_run_makes_claim_stale(self):
        h = Harness().run(term("pytest", 0, "5 passed"), patch("src/app.py"))
        f = h.report("All tests pass.").findings[0]
        self.assertEqual(f.status, checker.STALE)
        self.assertIn("src/app.py", f.reason)

    def test_passing_run_after_edits_verifies(self):
        h = Harness().run(patch("src/app.py"), term("python -m pytest tests/", 0, "5 passed in 0.2s"))
        self.assertEqual(h.status_of("All 5 tests pass."), checker.VERIFIED)

    def test_no_test_run_is_unverified(self):
        h = Harness().run(patch("src/app.py"))
        f = h.report("The tests should pass now.").findings[0]
        self.assertEqual(f.status, checker.UNVERIFIED)
        self.assertTrue(f.hedged)

    def test_runner_that_found_no_tests_is_unverified(self):
        h = Harness().run(term("npm test", 0, "No tests found, exiting with code 0"))
        self.assertEqual(h.status_of("Tests pass."), checker.UNVERIFIED)

    def test_background_run_is_unverified(self):
        h = Harness().run(term("pytest", None, "", background=True))
        self.assertEqual(h.status_of("All tests pass."), checker.UNVERIFIED)

    def test_edit_claims_check_each_path(self):
        h = Harness().run(write("src/app.py"), ("read_file", {"path": "README.md"}, "{}", "ok"))
        report = h.report("I updated `src/app.py` and `README.md`.")
        by_target = {f.target: f for f in report.findings if f.kind == "edit"}
        self.assertEqual(by_target["src/app.py"].status, checker.VERIFIED)
        self.assertEqual(by_target["README.md"].status, checker.UNVERIFIED)
        self.assertIn("only read", by_target["README.md"].reason)

    def test_failed_write_contradicts_edit_claim(self):
        h = Harness().run(write("/etc/hosts", ok=False))
        self.assertEqual(h.status_of("I updated /etc/hosts.", "edit"), checker.CONTRADICTED)

    def test_shell_redirect_counts_as_write(self):
        h = Harness().run(term("echo 'x=1' >> config/settings.ini", 0))
        self.assertEqual(h.status_of("I added the flag to config/settings.ini.", "edit"), checker.VERIFIED)

    def test_v4a_patch_paths_are_recorded(self):
        body = "*** Begin Patch\n*** Update File: lib/a.rb\n@@\n-x\n+y\n*** Add File: lib/b.rb\n+z\n*** End Patch"
        h = Harness().run(("patch", {"mode": "patch", "patch": body}, "{}", "ok"))
        self.assertEqual(h.status_of("I created lib/b.rb.", "edit"), checker.VERIFIED)

    def test_git_commit_and_rejected_push(self):
        h = Harness().run(
            term("git add -A && git commit -m 'fix'", 0, "[main abc123] fix"),
            term("git push origin main", 1, "! [rejected] main -> main (fetch first)"),
        )
        report = h.report("I committed the fix and pushed to origin.")
        status = {f.kind: f.status for f in report.findings}
        self.assertEqual(status["commit"], checker.VERIFIED)
        self.assertEqual(status["push"], checker.CONTRADICTED)

    def test_commit_followed_by_edit_is_stale(self):
        h = Harness().run(term("git commit -am wip", 0), patch("a.py"))
        self.assertEqual(h.status_of("Changes are committed.", "commit"), checker.STALE)

    def test_install_claim_checks_package_name(self):
        h = Harness().run(term("pip install requests==2.32.3", 0, "Successfully installed requests-2.32.3"))
        self.assertEqual(h.status_of("I installed `requests`.", "install"), checker.VERIFIED)
        self.assertEqual(h.status_of("I installed `httpx`.", "install"), checker.UNVERIFIED)

    def test_ran_claim(self):
        h = Harness().run(term("make lint", 2, "error"))
        self.assertEqual(h.status_of("I ran `make lint`.", "ran"), checker.VERIFIED)
        self.assertEqual(h.status_of("I ran `make test`.", "ran"), checker.UNVERIFIED)

    def test_verified_claim_needs_a_run_after_the_last_edit(self):
        h = Harness().run(term("python app.py", 0, "ok"), patch("app.py"))
        self.assertEqual(h.status_of("I tested it and it works.", "verified"), checker.STALE)
        h.run(term("python app.py", 0, "ok"))
        self.assertEqual(h.status_of("I tested it and it works.", "verified"), checker.VERIFIED)

    def test_checked_claim_can_rest_on_a_read(self):
        h = Harness()
        self.assertEqual(h.status_of("I checked the logs and the error is a timeout.", "verified"), checker.UNVERIFIED)
        h.run(("read_file", {"path": "logs/app.log"}, "{}", "ok"))
        self.assertEqual(h.status_of("I checked the logs and the error is a timeout.", "verified"), checker.VERIFIED)
        self.assertEqual(h.status_of("I tested it.", "verified"), checker.UNVERIFIED)

    def test_install_claim_without_a_name(self):
        claims = [c for c in extract_claims("I installed the dependencies.") if c.kind == "install"]
        self.assertEqual(claims[0].target, "")
        h = Harness().run(term("npm ci", 0, "added 120 packages"))
        self.assertEqual(h.status_of("I installed the dependencies.", "install"), checker.VERIFIED)

    def test_build_passing(self):
        h = Harness().run(term("npm run build", 1, "error TS2304: Cannot find name 'x'.\nFound 1 error."))
        self.assertEqual(h.status_of("The build is now passing.", "build"), checker.CONTRADICTED)

    def test_commit_message_text_is_not_a_failure(self):
        h = Harness().run(term("git commit -m 'fix 2 failed tests'", 0, "[main 1a2b] fix 2 failed tests"))
        self.assertEqual(h.status_of("I committed the fix.", "commit"), checker.VERIFIED)


class HookTests(unittest.TestCase):
    def test_pre_verify_sends_agent_back_with_the_contradiction(self):
        h = Harness().run(patch("app.py"), term("pytest", 1, "2 failed"))
        out = h.plugin.on_pre_verify(session_id=SID, attempt=0, final_response="Done. All tests pass.",
                                     changed_paths=["app.py"], coding=True)
        self.assertEqual(out["action"], "continue")
        self.assertIn("[Vouch]", out["message"])
        self.assertIn("exited 1", out["message"])
        self.assertEqual(h.ctx.state.get("stats")["nudges"], 1)

    def test_pre_verify_is_bounded_and_respects_mode(self):
        h = Harness().run(patch("app.py"))
        self.assertIsNone(h.plugin.on_pre_verify(session_id=SID, attempt=1, final_response="All tests pass."))
        h2 = Harness({"claims_mode": "annotate"}).run(patch("app.py"))
        self.assertIsNone(h2.plugin.on_pre_verify(session_id=SID, attempt=0, final_response="All tests pass."))
        h3 = Harness({"claims_max_nudges": 3}).run(patch("app.py"))
        self.assertIsNotNone(h3.plugin.on_pre_verify(session_id=SID, attempt=2, final_response="All tests pass."))

    def test_pre_verify_quiet_when_claims_hold(self):
        h = Harness().run(patch("app.py"), term("pytest", 0, "3 passed"))
        self.assertIsNone(h.plugin.on_pre_verify(session_id=SID, attempt=0, final_response="All tests pass."))

    def test_transform_appends_footer_only_for_problems(self):
        h = Harness().run(patch("app.py"), term("pytest", 0, "3 passed"))
        self.assertIsNone(h.plugin.on_transform_llm_output(response_text="All tests pass.", session_id=SID))
        out = h.plugin.on_transform_llm_output(response_text="All tests pass. I pushed to origin.", session_id=SID)
        self.assertTrue(out.startswith("All tests pass. I pushed to origin."))
        self.assertIn("**Vouch**", out)
        self.assertIn("no git push command ran", out)
        stats = h.ctx.state.get("stats")
        self.assertEqual(stats["verified"], 2)
        self.assertEqual(stats["unverified"], 1)
        self.assertEqual(h.ctx.state.get("recent")[-1]["kind"], "push")

    def test_transform_respects_footer_and_off(self):
        h = Harness({"footer": False}).run(patch("a.py"))
        self.assertIsNone(h.plugin.on_transform_llm_output(response_text="All tests pass.", session_id=SID))
        h2 = Harness({"claims_mode": "off"}).run(patch("a.py"))
        self.assertIsNone(h2.plugin.on_transform_llm_output(response_text="All tests pass.", session_id=SID))

    def test_footer_verified_lists_passing_claims(self):
        h = Harness({"claims_footer_verified": True}).run(term("pytest", 0, "3 passed"))
        out = h.plugin.on_transform_llm_output(response_text="All tests pass.", session_id=SID)
        self.assertIn("✅", out)

    def test_kinds_filter(self):
        h = Harness({"claims_kinds": ["push"]}).run(patch("a.py"))
        self.assertIsNone(h.plugin.on_transform_llm_output(response_text="All tests pass.", session_id=SID))

    def test_sessions_are_separate(self):
        h = Harness().run(term("pytest", 0, "1 passed"), sid="a")
        self.assertEqual(checker.check_reply("Tests pass.", h.plugin.ledger.events("b")).findings[0].status,
                         checker.UNVERIFIED)

    def test_own_tools_are_not_recorded(self):
        h = Harness().run(("vouch_check", {"text": "x"}, "{}", "ok"))
        self.assertEqual(h.plugin.ledger.events(SID), [])

    def test_session_delete_forgets(self):
        h = Harness().run(term("pytest", 0, "1 passed"))
        h.plugin.on_session_delete(session_id=SID)
        self.assertEqual(h.plugin.ledger.events(SID), [])

    def test_hooks_never_raise(self):
        h = Harness()
        h.plugin.on_post_tool_call(tool_name="terminal", args=None, result=object(), session_id=SID)
        self.assertIsNone(h.plugin.on_pre_verify(session_id=SID, attempt=0, final_response=None))
        self.assertIsNone(h.plugin.on_transform_llm_output(response_text=None, session_id=SID))


class ToolAndCommandTests(unittest.TestCase):
    def test_evidence_tool(self):
        h = Harness().run(patch("a.py"), term("pytest", 1, "1 failed"), term("ls", 0, "a.py"))
        data = json.loads(h.plugin.tool_evidence({"kind": "failures"}, session_id=SID))
        self.assertEqual(data["total_events"], 3)
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(data["events"][0]["exit_code"], 1)
        self.assertIn("output_tail", data["events"][0])
        data = json.loads(h.plugin.tool_evidence({"kind": "edits"}, session_id=SID))
        self.assertEqual(data["events"][0]["paths"], ["a.py"])

    def test_check_tool(self):
        h = Harness().run(term("pytest", 1, "1 failed"))
        data = json.loads(h.plugin.tool_check({"text": "All tests pass."}, session_id=SID))
        self.assertEqual(data["counts"]["contradicted"], 1)
        self.assertIn("error", json.loads(h.plugin.tool_check({}, session_id=SID)))

    def test_tools_fall_back_to_latest_session(self):
        h = Harness().run(term("pytest", 0, "1 passed"), sid="real")
        data = json.loads(h.plugin.tool_evidence({}, session_id=None))
        self.assertEqual(data["total_events"], 1)

    def test_slash_command(self):
        h = Harness().run(term("pytest", 1, "1 failed"))
        h.plugin.on_transform_llm_output(response_text="All tests pass.", session_id=SID)
        self.assertIn("contradicted", h.plugin.command("", session_id=SID))
        self.assertIn("pytest", h.plugin.command("evidence", session_id=SID))
        self.assertIn("1 claims checked", h.plugin.command("stats", session_id=SID))
        self.assertIn("Usage", h.plugin.command("help", session_id=SID))

    def test_cli_handler(self):
        h = Harness().run(term("pytest", 1, "1 failed"))
        h.plugin.on_transform_llm_output(response_text="All tests pass.", session_id=SID)
        parser = argparse.ArgumentParser()
        vouch._cli_setup(parser)
        handler = vouch._make_cli_handler(h.ctx)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(handler(parser.parse_args([])), 0)
        self.assertIn("contradicted 1", buf.getvalue())
        with redirect_stdout(io.StringIO()):
            handler(parser.parse_args(["reset"]))
        self.assertEqual(h.ctx.state.get("stats"), {})


class RegistrationTests(unittest.TestCase):
    def test_contract_registers_the_claim_check(self):
        ctx = FakeCtx()
        contract.register(ctx)
        for name in ("vouch_evidence", "vouch_check"):
            self.assertIn(name, ctx.tools)
        self.assertIn("vouch", ctx.commands)
        self.assertIn("vouch", ctx.cli)
        manifest = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
        for key in ("claims_mode", "claims_max_nudges", "claims_footer_verified", "claims_kinds", "footer"):
            self.assertIn(key, manifest["config_schema"])

    def test_no_forbidden_calls(self):
        """Catalog rules 9, 11 and 13: no core overrides, no secret files, no network or shell."""
        import ast

        banned_modules = {"subprocess", "socket", "urllib", "http", "requests", "httpx", "aiohttp", "ctypes"}
        banned_names = {"eval", "exec", "compile", "__import__", "setattr"}   # builtins
        banned_attrs = {"system", "popen", "spawn", "execv", "fork"}            # os.*
        for path in PLUGIN.glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], banned_modules, path.name)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    self.assertNotIn(node.module.split(".")[0], banned_modules, path.name)
                elif isinstance(node, ast.Call):
                    fn = node.func
                    if isinstance(fn, ast.Name):
                        self.assertNotIn(fn.id, banned_names, f"{fn.id}() in {path.name}")
                    elif isinstance(fn, ast.Attribute):
                        self.assertNotIn(fn.attr, banned_attrs, f".{fn.attr}() in {path.name}")

if __name__ == "__main__":
    unittest.main()
