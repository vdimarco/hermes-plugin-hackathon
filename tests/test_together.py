"""Vouch and Contract together: Hermes keeps only the first reply rewrite, so they share one footer."""

import json
import unittest

from tests._load import FakeCtx, load, submodule

vouch = load("vouch")
contract = load("contract")
VLedger = submodule("vouch", "ledger").Ledger
CLedger = submodule("contract", "ledger").Ledger
SID = "s1"


def pair(contract_config=None):
    holder = {}

    def dispatch(tool, args, **kwargs):  # what ctx.dispatch_tool reaches through the tool registry
        assert tool == "contract_status"
        return holder["c"].tool_status(args, **kwargs)

    cctx = FakeCtx(contract_config, plugins={"vouch", "contract"})
    vctx = FakeCtx(dispatch=dispatch, plugins={"vouch", "contract"})
    c = contract.ContractPlugin(cctx, ledger=CLedger())
    v = vouch.Vouch(vctx, ledger=VLedger())
    holder["c"] = c
    return v, c


def tool(v, c, name, args, result="{}", status="ok"):
    for p in (v, c):
        p.on_post_tool_call(tool_name=name, args=args, result=result, status=status, session_id=SID)


class Together(unittest.TestCase):
    def test_contract_yields_and_vouch_carries_its_line(self):
        v, c = pair()
        c.tool_propose({"title": "Fix discount", "checks": ["pytest -q"]}, session_id=SID)
        c.command("approve", session_id=SID)
        self.assertIsNone(c.on_transform_llm_output(response_text="Working on it.", session_id=SID))
        out = v.on_transform_llm_output(response_text="Working on it.", session_id=SID)
        self.assertIn("📜 Contract “Fix discount”: 0/1 checks pass", out)

    def test_one_footer_with_both_parts(self):
        v, c = pair()
        c.tool_propose({"title": "Fix discount", "checks": ["pytest -q"]}, session_id=SID)
        c.command("approve", session_id=SID)
        tool(v, c, "terminal", {"command": "pytest -q"}, json.dumps({"output": "1 passed", "exit_code": 0}))
        out = v.on_transform_llm_output(response_text="All tests pass and I pushed to origin.", session_id=SID)
        self.assertIn("**Vouch**", out)
        self.assertIn("no git push command ran", out)
        self.assertIn("✅ Contract met: “Fix discount”", out)
        self.assertEqual(out.count("---"), 1)  # one footer, not two
        again = v.on_transform_llm_output(response_text="Anything else?", session_id=SID)
        self.assertIsNone(again)  # "met" is announced once

    def test_contract_line_even_when_vouch_is_off(self):
        holder = {}
        v_ctx = FakeCtx({"mode": "off"}, dispatch=lambda t, a, **k: holder["c"].tool_status(a, **k), plugins={"vouch", "contract"})
        v = vouch.Vouch(v_ctx, ledger=VLedger())
        c = contract.ContractPlugin(FakeCtx(plugins={"vouch", "contract"}), ledger=CLedger())
        holder["c"] = c
        c.tool_propose({"title": "T", "checks": ["pytest -q"]}, session_id=SID)
        self.assertIn("Contract proposed", v.on_transform_llm_output(response_text="Plan.", session_id=SID))

    def test_contract_footer_setting_is_respected(self):
        v, c = pair({"footer": False})
        c.tool_propose({"title": "T", "checks": ["pytest -q"]}, session_id=SID)
        self.assertIsNone(v.on_transform_llm_output(response_text="Plan.", session_id=SID))

    def test_footer_request_is_strict_about_the_session(self):
        v, c = pair()
        c.tool_propose({"title": "Other chat", "checks": ["pytest -q"]}, session_id="other")
        self.assertIsNone(v.on_transform_llm_output(response_text="Hello.", session_id=SID))

    def test_contract_alone_still_writes_its_footer(self):
        c = contract.ContractPlugin(FakeCtx(plugins={"contract"}), ledger=CLedger())
        c.tool_propose({"title": "T", "checks": ["pytest -q"]}, session_id=SID)
        self.assertIn("Contract proposed", c.on_transform_llm_output(response_text="Plan.", session_id=SID))

    def test_status_tool_reports_summary_and_receipt(self):
        v, c = pair()
        c.tool_propose({"title": "T", "checks": ["pytest -q"]}, session_id=SID)
        data = json.loads(c.tool_status({}, session_id=SID))
        self.assertEqual(data["summary"], "0/1 checks pass")
        self.assertEqual(data["receipt_hash"], "")


if __name__ == "__main__":
    unittest.main()
