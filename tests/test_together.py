"""Contract and its claim check (Vouch) share one tool record and write one reply footer.

Hermes keeps only the first reply rewrite, so the footer must hold both parts.
"""

import json
import unittest

from tests._load import FakeCtx, load, submodule

contract = load("contract")
Ledger = submodule("contract", "ledger").Ledger
SID = "s1"


def plugin(config=None):
    return contract.ContractPlugin(FakeCtx(config), ledger=Ledger())


def tool(p, name, args, result="{}", status="ok"):
    p.on_post_tool_call(tool_name=name, args=args, result=result, status=status, session_id=SID)


def passing_contract(p, title="Fix discount"):
    p.tool_propose({"title": title, "checks": ["pytest -q"]}, session_id=SID)
    p.command("approve", session_id=SID)


class Together(unittest.TestCase):
    def test_one_footer_with_both_parts(self):
        p = plugin()
        passing_contract(p)
        tool(p, "terminal", {"command": "pytest -q"}, json.dumps({"output": "1 passed", "exit_code": 0}))
        out = p.on_transform_llm_output(response_text="All tests pass and I pushed to origin.", session_id=SID)
        self.assertIn("**Vouch**", out)
        self.assertIn("no git push command ran", out)
        self.assertIn("✅ Contract met: “Fix discount”", out)
        self.assertEqual(out.count("---"), 1)  # one footer, not two
        self.assertIsNone(p.on_transform_llm_output(response_text="Anything else?", session_id=SID))  # "met" once

    def test_contract_line_alone(self):
        p = plugin()
        passing_contract(p)
        out = p.on_transform_llm_output(response_text="Working on it.", session_id=SID)
        self.assertIn("\n---\n📜 Contract “Fix discount”: 0/1 checks pass", out)

    def test_claims_alone_without_a_contract(self):
        p = plugin()
        tool(p, "terminal", {"command": "pytest -q"}, json.dumps({"output": "2 failed", "exit_code": 1}))
        out = p.on_transform_llm_output(response_text="All tests pass.", session_id=SID)
        self.assertIn("**Vouch**", out)
        self.assertIn("❌", out)
        self.assertNotIn("Contract", out)

    def test_claims_off_keeps_the_contract_line(self):
        p = plugin({"claims_mode": "off"})
        p.tool_propose({"title": "T", "checks": ["pytest -q"]}, session_id=SID)
        out = p.on_transform_llm_output(response_text="All tests pass.", session_id=SID)
        self.assertIn("Contract proposed", out)
        self.assertNotIn("Vouch", out)

    def test_footer_off_still_finishes_the_contract(self):
        p = plugin({"footer": False})
        passing_contract(p)
        tool(p, "terminal", {"command": "pytest -q"}, json.dumps({"output": "1 passed", "exit_code": 0}))
        self.assertIsNone(p.on_transform_llm_output(response_text="All tests pass. Pushed.", session_id=SID))
        self.assertEqual(json.loads(p.tool_status({}, session_id=SID))["status"], "met")

    def test_other_sessions_stay_out_of_the_footer(self):
        p = plugin()
        p.tool_propose({"title": "Other chat", "checks": ["pytest -q"]}, session_id="other")
        self.assertIsNone(p.on_transform_llm_output(response_text="Hello.", session_id=SID))

    def test_pre_verify_contract_gate_first_then_claims(self):
        p = plugin()
        passing_contract(p)
        tool(p, "patch", {"path": "cart.py"})
        gate = p.on_pre_verify(session_id=SID, attempt=0, final_response="Done. Pushed to origin.")
        self.assertIn("pytest -q", gate["message"])          # the contract gate speaks first
        self.assertNotIn("[Vouch]", gate["message"])
        tool(p, "terminal", {"command": "pytest -q"}, json.dumps({"output": "1 passed", "exit_code": 0}))
        nudge = p.on_pre_verify(session_id=SID, attempt=0, final_response="Done. Pushed to origin.")
        self.assertIn("[Vouch]", nudge["message"])           # then the claim check
        self.assertIn("push", nudge["message"])
        self.assertIsNone(p.on_pre_verify(session_id=SID, attempt=0, final_response="Done. The tests pass."))

    def test_one_record_for_both(self):
        p = plugin()
        tool(p, "terminal", {"command": "pytest -q"}, json.dumps({"output": "1 passed", "exit_code": 0}))
        self.assertEqual(len(p.ledger.events(SID)), 1)
        self.assertIs(p.vouch.ledger, p.ledger)
        tool(p, "vouch_check", {"text": "x"})
        tool(p, "contract_status", {})
        self.assertEqual(len(p.ledger.events(SID)), 1)  # the plugin's own tools are not evidence

    def test_contract_run_counts_as_evidence(self):
        def dispatch(name, args, **kwargs):
            if "rev-parse" in args["command"]:
                return json.dumps({"output": "head=abc\ntree=def\ndirty=0", "exit_code": 0})
            return json.dumps({"output": "3 passed", "exit_code": 0})

        p = contract.ContractPlugin(FakeCtx(dispatch=dispatch), ledger=Ledger())
        passing_contract(p)
        self.assertEqual(json.loads(p.tool_run({}, session_id=SID))["status"], "met")
        data = json.loads(p.vouch.tool_check({"text": "All tests pass."}, session_id=SID))
        self.assertEqual(data["counts"]["verified"], 1)

    def test_session_delete_clears_both(self):
        p = plugin()
        passing_contract(p)
        tool(p, "terminal", {"command": "pytest -q"}, json.dumps({"output": "1 passed", "exit_code": 0}))
        p.on_transform_llm_output(response_text="All tests pass.", session_id=SID)
        p.on_session_delete(session_id=SID)
        self.assertEqual(p.ledger.events(SID), [])
        self.assertNotIn(SID, p.vouch.reports)
        self.assertIsNone(p.get(SID))

    def test_status_tool_reports_summary_and_receipt(self):
        p = plugin()
        p.tool_propose({"title": "T", "checks": ["pytest -q"]}, session_id=SID)
        data = json.loads(p.tool_status({}, session_id=SID))
        self.assertEqual(data["summary"], "0/1 checks pass")
        self.assertEqual(data["receipt_hash"], "")


if __name__ == "__main__":
    unittest.main()
