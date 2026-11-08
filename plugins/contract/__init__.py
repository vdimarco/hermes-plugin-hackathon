"""Contract: the agent writes the exam before it takes it, cannot change the exam,
and cannot claim it passed when it did not.

Before a coding task, the agent (or the user) writes a contract: shell checks that
pass only when the task is done, plus locked paths (usually the tests). The user
approves it with /contract approve. From then on:

* pre_tool_call blocks writes to locked paths (write_file, patch, shell writes such
  as sed -i, >, rm, mv, git checkout, and file writes in execute_code). Subagents
  started from the session (subagent_start) inherit the locks.
* pre_llm_call reminds the agent of the definition of done on every turn.
* post_tool_call records every run of a check command, with its exit code.
* pre_verify does not let a turn that edited code finish while a check fails, has
  not run, or passed before the last edit.
* contract_run runs the checks through the Hermes terminal tool (so approvals and
  the configured backend apply) and, when all pass, writes a receipt: a JSON record
  with a hash chain and the git tree id of the checked code.
* /contract receipt prints a block for the PR description; `hermes contract verify`
  checks the hashes and prints a script that runs the checks again.

Vouch, the claim check (vouch.py), runs on every turn, with or without a contract:
it reads the final reply, finds claims such as "all tests pass" or "pushed to
origin", and checks each one against the same tool record. A false claim sends the
agent back once; what is still unsupported goes in the reply footer, next to the
contract's status line. Hermes keeps only the first reply rewrite, so one plugin
writes one footer.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from . import desktop_events, model, schemas
from .ledger import Ledger
from .vouch import OWN_TOOLS as VOUCH_TOOLS, Vouch
from .vouch import _cli_setup as _vouch_cli_setup, _make_cli_handler as _vouch_cli_handler

logger = logging.getLogger(__name__)

VERSION = "1.2.0"
TOOLSET = "contract"
OWN_TOOLS = {"contract_propose", "contract_status", "contract_run"} | VOUCH_TOOLS
MAX_CONTRACTS = 32
MAX_RECEIPTS = 20
_WORKSPACE_CMD = (
    "printf 'head=%s\\n' \"$(git rev-parse HEAD 2>/dev/null)\"; "
    "s=$(git stash create 2>/dev/null); "
    "printf 'tree=%s\\n' \"$(git rev-parse \"${s:-HEAD}^{tree}\" 2>/dev/null)\"; "
    "printf 'dirty=%s\\n' \"$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')\""
)


def _parse(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    try:
        data = json.loads(result) if isinstance(result, str) else {}
    except (TypeError, ValueError):
        return {"output": str(result)}
    return data if isinstance(data, dict) else {"output": str(result)}


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(value)


class ContractPlugin:
    def __init__(self, ctx: Any, ledger: Optional[Ledger] = None) -> None:
        self.ctx = ctx
        self.ledger = ledger or Ledger()
        self._lock = threading.RLock()
        self._contracts: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._loaded = False
        self._models: Dict[str, str] = {}
        self._parents: Dict[str, str] = {}   # subagent session -> parent session key
        self.vouch = Vouch(ctx, ledger=self.ledger)  # the claim check reads the same tool record

    # -- config ---------------------------------------------------------------
    def _cfg(self, key: str, default: Any) -> Any:
        try:
            value = self.ctx.get_config(key, default)
        except Exception:
            return default
        return default if value is None else value

    def _max_nudges(self) -> int:
        try:
            return max(0, int(self._cfg("max_nudges", 2)))
        except (TypeError, ValueError):
            return 2

    # -- persistence ----------------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            stored = self.ctx.state.get("contracts", None) or {}
            for key, contract in stored.items():
                if isinstance(contract, dict) and contract.get("checks"):
                    self._contracts[key] = contract
        except Exception:
            logger.debug("contract: cannot read state", exc_info=True)

    def _save(self) -> None:
        try:
            while len(self._contracts) > MAX_CONTRACTS:
                self._contracts.popitem(last=False)
            self.ctx.state.set("contracts", dict(self._contracts))
        except Exception:
            logger.debug("contract: cannot write state", exc_info=True)

    def _store_receipt(self, receipt: Dict[str, Any]) -> None:
        try:
            receipts = list(self.ctx.state.get("receipts", None) or [])
            receipts.append(receipt)
            self.ctx.state.set("receipts", receipts[-MAX_RECEIPTS:])
        except Exception:
            logger.debug("contract: cannot store receipt", exc_info=True)

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._load()
            contract = self._contracts.get(key)
            return contract if contract and contract.get("status") != model.CANCELLED else None

    def _put(self, key: str, contract: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            self._load()
            if contract is None:
                self._contracts.pop(key, None)
            else:
                self._contracts[key] = contract
                self._contracts.move_to_end(key)
            self._save()
        self._publish(key, contract)

    def snapshot(self, key: str, contract: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """What the Desktop half shows for one session."""
        if not contract or contract.get("status") == model.CANCELLED:
            return {"session": key, "status": "none", "ts": int(time.time())}
        last_write = self.last_write_seq(key)
        receipt = contract.get("receipt") or {}
        return {
            "session": key,
            "id": contract["id"],
            "title": contract["title"],
            "status": contract["status"],
            "summary": model.summary_line(contract, last_write),
            "checks": [{"id": c["id"], "name": c["name"], "command": c["command"], "state": state,
                        "reason": (r or {}).get("reason", "")}
                       for c, state, r in model.check_states(contract, last_write)],
            "locked": contract["locked"][:16],
            "approved_by": contract.get("approved_by"),
            "receipt": {"hash": receipt.get("receipt_hash", ""), "tree": (receipt.get("workspace") or {}).get("tree", ""),
                        "stale": bool(receipt.get("stale"))} if receipt else None,
            "ts": int(time.time()),
        }

    def _publish(self, key: str, contract: Optional[Dict[str, Any]]) -> None:
        try:
            desktop_events.emit("state", self.snapshot(key, contract))
        except Exception:
            logger.debug("contract: snapshot failed", exc_info=True)

    # -- session helpers ------------------------------------------------------
    @staticmethod
    def key(session_id: Optional[str], task_id: Optional[str] = None) -> str:
        return Ledger.key(session_id, task_id)

    def resolve(self, session_id: Optional[str], task_id: Optional[str] = None) -> str:
        key = self.key(session_id, task_id)
        if self.get(key) or self.ledger.events(key):
            return key
        with self._lock:
            self._load()
            live = [k for k, c in self._contracts.items() if c.get("status") != model.CANCELLED]
        return live[-1] if live else (self.ledger.latest_key() or key)

    def last_write_seq(self, key: str) -> int:
        writes = [e.seq for e in self.ledger.events(key) if e.is_write and e.ok]
        return max(writes) if writes else 0

    def _max_seq(self, key: str) -> int:
        events = self.ledger.events(key)
        return events[-1].seq if events else 0

    def _changed_paths(self, key: str, since: int) -> List[str]:
        out: List[str] = []
        for e in self.ledger.events(key):
            if e.seq > since and e.is_write and e.ok:
                for p in e.paths or ([f"$ {e.command[:60]}"] if e.command else []):
                    if p not in out:
                        out.append(p)
        return out

    def _refresh(self, key: str) -> Optional[Dict[str, Any]]:
        """A met contract goes back to active when files change after the receipt."""
        contract = self.get(key)
        if contract and contract["status"] == model.MET and not model.all_pass(contract, self.last_write_seq(key)):
            contract["status"] = model.ACTIVE
            if contract.get("receipt"):
                contract["receipt"]["stale"] = True
            contract["announced"] = False
            self._put(key, contract)
        return contract

    def _finish(self, key: str, contract: Dict[str, Any], workspace: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        receipt = model.build_receipt(
            contract, session_id=key, changed_paths=self._changed_paths(key, contract.get("activated_seq", 0)),
            workspace=workspace, model=self._models.get(key, ""), plugin_version=VERSION,
        )
        contract["status"] = model.MET
        contract["receipt"] = receipt
        contract["announced"] = False
        self._put(key, contract)
        self._store_receipt(receipt)
        return receipt

    def _record_result(self, key: str, contract: Dict[str, Any], event, output: str, source: str) -> List[Dict[str, Any]]:
        out = []
        for check in model.match_check(contract, event.command):
            passed, reason = model.judge(check, event.exit_code, output, event.background, event.failure_in_output)
            result = model.make_result(check, event.seq, event.command, event.exit_code, output, passed, reason, source)
            contract.setdefault("results", {})[check["id"]] = result
            out.append(result)
        if out:
            self._put(key, contract)
        return out

    # -- hooks ----------------------------------------------------------------
    def on_post_tool_call(self, tool_name: str = "", args: Any = None, result: Any = None,
                          session_id: Optional[str] = None, task_id: Optional[str] = None,
                          status: Optional[str] = None, turn_id: Optional[str] = None, **kwargs: Any) -> None:
        if not tool_name or tool_name in OWN_TOOLS:
            return
        try:
            key = self.key(session_id, task_id)
            event = self.ledger.record(key, tool_name, args, result, status=status, turn_id=turn_id or "")
            contract = self.get(key)
            if not contract:
                return
            recorded = []
            if tool_name == "terminal":
                output = str(_parse(result).get("output") or "")
                recorded = self._record_result(key, contract, event, output, "observed")
            if not recorded and event.is_write and event.ok and contract.get("results"):
                self._publish(key, contract)  # a write can turn passing checks stale
        except Exception:
            logger.debug("contract: post_tool_call failed", exc_info=True)

    def on_subagent_start(self, parent_session_id: Optional[str] = None, child_session_id: Optional[str] = None,
                          **kwargs: Any) -> None:
        """Subagents inherit the parent's locks (their check runs stay their own)."""
        if parent_session_id and child_session_id:
            parent = self._parents.get(str(parent_session_id), self.key(parent_session_id))
            self._parents[str(child_session_id)] = parent
            while len(self._parents) > 512:
                self._parents.pop(next(iter(self._parents)))

    def on_pre_tool_call(self, tool_name: str = "", args: Any = None, session_id: Optional[str] = None,
                         task_id: Optional[str] = None, **kwargs: Any) -> Optional[Dict[str, str]]:
        try:
            if tool_name in OWN_TOOLS:
                return None
            key = self.key(session_id, task_id)
            contract = self.get(key) or (self.get(self._parents[key]) if key in self._parents else None)
            if not contract or contract["status"] not in (model.ACTIVE, model.MET):
                return None
            hit = model.lock_violation(tool_name, args if isinstance(args, dict) else {}, contract["locked"])
            if not hit:
                return None
            path, lock = hit
            desktop_events.emit("blocked", {"session": key, "tool": tool_name, "path": str(path)[:300],
                                            "lock": lock, "title": contract["title"], "ts": int(time.time())})
            return {"action": "block", "message": (
                f"Contract lock: {path} is covered by the approved contract “{contract['title']}” (lock '{lock}'). "
                "Make the checks pass by fixing the code, not the checks. If the contract is wrong, tell the user; "
                "only the user can change it (/contract unlock or /contract reset)."
            )}
        except Exception:
            logger.debug("contract: pre_tool_call failed", exc_info=True)
            return None

    def on_pre_llm_call(self, session_id: Optional[str] = None, **kwargs: Any) -> Optional[Dict[str, str]]:
        try:
            contract = self._refresh(self.key(session_id))
            if contract and contract["status"] in (model.PROPOSED, model.ACTIVE):
                return {"context": model.format_brief(contract)}
        except Exception:
            logger.debug("contract: pre_llm_call failed", exc_info=True)
        return None

    def on_pre_verify(self, session_id: str = "", attempt: int = 0, **kwargs: Any) -> Optional[Dict[str, str]]:
        """The contract gate comes first; when it lets the turn end, Vouch checks the claims."""
        gate = self._contract_gate(session_id, attempt, **kwargs)
        if gate:
            return gate
        return self.vouch.on_pre_verify(session_id=session_id, attempt=attempt, **kwargs)

    def _contract_gate(self, session_id: str = "", attempt: int = 0, **kwargs: Any) -> Optional[Dict[str, str]]:
        try:
            key = self.key(session_id)
            if kwargs.get("model"):
                self._models[key] = str(kwargs["model"])
            contract = self._refresh(key)
            if not contract or contract["status"] != model.ACTIVE:
                return None
            last_write = self.last_write_seq(key)
            if model.all_pass(contract, last_write):
                self._finish(key, contract)
                return None
            if attempt >= self._max_nudges():
                return None
            return {"action": "continue", "message": model.format_gate_message(contract, last_write)}
        except Exception:
            logger.debug("contract: pre_verify failed", exc_info=True)
            return None

    def footer_line(self, key: str) -> str:
        """The one-line contract status for the reply footer ("" when there is nothing to say).
        Moves an all-passing contract to met, and announces "met" only once."""
        contract = self._refresh(key)
        if not contract:
            return ""
        last_write = self.last_write_seq(key)
        if contract["status"] == model.ACTIVE and model.all_pass(contract, last_write):
            self._finish(key, contract)
        if contract["status"] == model.PROPOSED:
            return (f"📝 Contract proposed: “{contract['title']}” ({len(contract['checks'])} checks). "
                    "Run `/contract` to read it and `/contract approve` to lock it.")
        if contract["status"] == model.ACTIVE:
            return f"📜 Contract “{contract['title']}”: {model.summary_line(contract, last_write)}. The task is not done yet."
        if contract["status"] == model.MET and not contract.get("announced"):
            receipt = contract.get("receipt") or {}
            contract["announced"] = True
            self._put(key, contract)
            return (f"✅ Contract met: “{contract['title']}” · {model.summary_line(contract, last_write)} · "
                    f"receipt `{receipt.get('receipt_hash', '')[:12]}` · `/contract receipt` for the PR block")
        return ""

    def on_transform_llm_output(self, response_text: str = "", session_id: str = "", **kwargs: Any) -> Optional[str]:
        """One footer: Vouch's claim verdicts, then the contract's status line."""
        if not isinstance(response_text, str) or not response_text.strip():
            return None
        try:
            key = self.key(session_id)
            if kwargs.get("model"):
                self._models[key] = str(kwargs["model"])
            claims = self.vouch.footer(response_text, session_id)
            if _truthy(self._cfg("footer", True)):
                line = self.footer_line(key)
            else:
                line = ""
                contract = self._refresh(key)  # still move an all-passing contract to met
                if contract and contract["status"] == model.ACTIVE and model.all_pass(contract, self.last_write_seq(key)):
                    self._finish(key, contract)
            if claims and line:
                footer = claims + "\n\n" + line
            elif claims:
                footer = claims
            elif line:
                footer = "\n---\n" + line
            else:
                return None
            return response_text.rstrip() + "\n" + footer
        except Exception:
            logger.debug("contract: transform failed", exc_info=True)
            return None

    def on_session_delete(self, session_id: str = "", **kwargs: Any) -> None:
        key = self.key(session_id)
        self.vouch.on_session_delete(session_id=session_id)
        self.ledger.forget(key)
        self._put(key, None)

    # -- tools ----------------------------------------------------------------
    def tool_propose(self, args: Dict[str, Any], session_id: Optional[str] = None,
                     task_id: Optional[str] = None, **kwargs: Any) -> str:
        try:
            key = self.key(session_id, task_id)
            current = self.get(key)
            if current and current["status"] in (model.ACTIVE, model.MET):
                return json.dumps({"error": (
                    f"The contract “{current['title']}” is approved and locked. You cannot replace it. "
                    "If it is wrong, explain why and ask the user to run /contract reset.")})
            args = args or {}
            require = _truthy(self._cfg("require_approval", True))
            contract = model.new_contract(
                args.get("title", ""), list(args.get("checks") or []), list(args.get("locked_paths") or []),
                lock_tests=_truthy(self._cfg("lock_tests", True)), approved=not require, author="agent",
            )
            contract["activated_seq"] = self._max_seq(key)
            self._put(key, contract)
            next_step = ("Show the contract to the user and ask them to run /contract approve. It is not enforced until then."
                         if require else "The contract is active now (require_approval is off).")
            return json.dumps({"contract": _public(contract), "status": contract["status"], "next": next_step})
        except model.ContractError as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:
            return json.dumps({"error": f"contract_propose failed: {exc}"})

    def tool_status(self, args: Dict[str, Any], session_id: Optional[str] = None,
                    task_id: Optional[str] = None, **kwargs: Any) -> str:
        try:
            key = self.resolve(session_id, task_id)
            contract = self._refresh(key)
            if not contract:
                return json.dumps({"contract": None, "text": model.format_status(None, 0)})
            last_write = self.last_write_seq(key)
            states = [{"id": c["id"], "command": c["command"], "state": s, "reason": (r or {}).get("reason")}
                      for c, s, r in model.check_states(contract, last_write)]
            receipt = contract.get("receipt") or {}
            return json.dumps({"status": contract["status"], "title": contract["title"], "checks": states,
                               "summary": model.summary_line(contract, last_write), "locked": contract["locked"],
                               "receipt_hash": receipt.get("receipt_hash", ""), "receipt_stale": bool(receipt.get("stale")),
                               "text": model.format_status(contract, last_write)})
        except Exception as exc:
            return json.dumps({"error": f"contract_status failed: {exc}"})

    def tool_run(self, args: Dict[str, Any], session_id: Optional[str] = None,
                 task_id: Optional[str] = None, **kwargs: Any) -> str:
        try:
            key = self.resolve(session_id, task_id)
            contract = self.get(key)
            if not contract:
                return json.dumps({"error": "No contract in this session. Propose one with contract_propose first."})
            only = str((args or {}).get("check") or "").strip()
            checks = [c for c in contract["checks"] if not only or c["id"] == only or c["name"] == only]
            if not checks:
                return json.dumps({"error": f"No check named {only!r}."})
            ids = {"task_id": task_id, "session_id": session_id}
            ran = []
            for check in checks:
                call = {"command": check["command"]}
                if check.get("workdir"):
                    call["workdir"] = check["workdir"]
                try:
                    raw = self.ctx.dispatch_tool("terminal", call, **{k: v for k, v in ids.items() if v})
                except Exception as exc:
                    return json.dumps({"error": f"cannot run checks from the plugin ({exc}). "
                                                "Run each check command with the terminal tool; Contract records those runs too."})
                data = _parse(raw)
                event = self.ledger.record(key, "terminal", call, raw, status="ok" if not data.get("error") else "error")
                output = str(data.get("output") or "") + (f"\n{data['error']}" if data.get("error") else "")
                for result in self._record_result(key, contract, event, output, "contract_run"):
                    ran.append({"id": result["check_id"], "passed": result["passed"], "reason": result["reason"],
                                "output_tail": result["output_tail"][-300:] if not result["passed"] else ""})
            last_write = self.last_write_seq(key)
            response: Dict[str, Any] = {"ran": ran, "summary": model.summary_line(contract, last_write),
                                        "status": contract["status"]}
            if contract["status"] in (model.ACTIVE, model.MET) and model.all_pass(contract, last_write):
                workspace = self._workspace(ids, contract)
                receipt = self._finish(key, contract, workspace)
                response.update(status=model.MET, receipt_hash=receipt["receipt_hash"],
                                next="All checks pass. Tell the user the contract is met; /contract receipt prints the PR block.")
            elif contract["status"] == model.PROPOSED:
                response["next"] = "The contract is not approved yet. Ask the user to run /contract approve."
            else:
                response["next"] = "Fix the failing checks, then call contract_run again."
            return json.dumps(response)
        except Exception as exc:
            return json.dumps({"error": f"contract_run failed: {exc}"})

    def _workspace(self, ids: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
        call = {"command": _WORKSPACE_CMD}
        workdirs = [c["workdir"] for c in contract["checks"] if c.get("workdir")]
        if workdirs:
            call["workdir"] = workdirs[0]
        try:
            raw = self.ctx.dispatch_tool("terminal", call, **{k: v for k, v in ids.items() if v})
        except Exception:
            return {}
        info: Dict[str, Any] = {}
        for line in str(_parse(raw).get("output") or "").splitlines():
            m = re.match(r"^(head|tree|dirty)=(\S*)$", line.strip())
            if m and m.group(2):
                info[m.group(1)] = int(m.group(2)) if m.group(1) == "dirty" and m.group(2).isdigit() else m.group(2)
        return info

    # -- slash command --------------------------------------------------------
    def command(self, raw_args: str = "", session_id: Optional[str] = None, **kwargs: Any) -> str:
        raw = (raw_args or "").strip()
        sub, _, rest = raw.partition(" ")
        sub, rest = sub.lower(), rest.strip()
        key = self.resolve(session_id)
        contract = self._refresh(key)
        last_write = self.last_write_seq(key)
        if sub in ("", "status", "show"):
            return model.format_status(contract, last_write)
        if sub == "approve":
            if not contract:
                return "There is no contract to approve."
            if contract["status"] != model.PROPOSED:
                return f"The contract is already {contract['status']}."
            contract.update(status=model.ACTIVE, approved_at=int(time.time()), approved_by="user")
            self._put(key, contract)
            return "🔒 Contract approved and locked.\n" + model.format_status(contract, last_write)
        if sub in ("reset", "cancel", "drop"):
            if not contract:
                return "There is no contract to reset."
            contract["status"] = model.CANCELLED
            self._put(key, contract)
            return f"Contract “{contract['title']}” dropped. The agent can propose a new one."
        if sub == "add":
            if not rest:
                return "Usage: /contract add <shell command>"
            try:
                if not contract:
                    contract = model.new_contract("User checks", [rest], lock_tests=_truthy(self._cfg("lock_tests", True)),
                                                  approved=True, author="user")
                    contract["activated_seq"] = self._max_seq(key)
                else:
                    checks = contract["checks"] + [rest]
                    fresh = model.new_contract(contract["title"], checks, contract["locked"], lock_tests=False)
                    contract["checks"], contract["locked"] = fresh["checks"], fresh["locked"]
                    contract["hash"] = model.contract_hash(contract)
                    if contract["status"] == model.MET:
                        contract["status"] = model.ACTIVE
            except model.ContractError as exc:
                return f"Cannot add the check: {exc}"
            self._put(key, contract)
            return "Check added.\n" + model.format_status(contract, last_write)
        if sub in ("lock", "unlock"):
            if not contract or not rest:
                return f"Usage: /contract {sub} <path or glob> (needs a contract)"
            locks = [l for l in contract["locked"] if l != rest]
            if sub == "lock":
                locks.append(rest)
            contract["locked"] = locks
            contract["hash"] = model.contract_hash(contract)
            self._put(key, contract)
            return f"{'Locked' if sub == 'lock' else 'Unlocked'} {rest}.\n" + model.format_status(contract, last_write)
        if sub == "receipt":
            receipt = (contract or {}).get("receipt")
            if not receipt:
                return "No receipt yet. A receipt is written when every check passes (contract_run)."
            note = "\n\n⚠️ Files changed after this receipt. Run contract_run again for a fresh one." if receipt.get("stale") else ""
            clean = {k: v for k, v in receipt.items() if k != "stale"}
            return model.receipt_markdown(clean) + note
        return ("Usage: /contract                 status of this session's contract\n"
                "       /contract approve         lock the proposed contract\n"
                "       /contract add <command>   add a check (you are the author, so it is approved)\n"
                "       /contract lock|unlock <glob>\n"
                "       /contract receipt         PR block with the receipt\n"
                "       /contract reset           drop the contract")


def _public(contract: Dict[str, Any]) -> Dict[str, Any]:
    return {k: contract[k] for k in ("id", "title", "status", "checks", "locked", "hash")}


# ---------------------------------------------------------------------------
# CLI: hermes contract ...
# ---------------------------------------------------------------------------

def _cli_setup(parser) -> None:
    parser.add_argument("action", nargs="?", default="list", choices=["list", "show", "export", "verify"])
    parser.add_argument("target", nargs="?", default="", help="receipt hash prefix (show/export) or file (verify)")
    parser.add_argument("--out", default="", help="export: write the receipt to this file")
    parser.add_argument("--script", action="store_true", help="verify: print a bash script that re-runs the checks")


def _find_receipt(ctx: Any, prefix: str) -> Optional[Dict[str, Any]]:
    receipts = list(ctx.state.get("receipts", None) or [])
    if not prefix:
        return receipts[-1] if receipts else None
    for r in reversed(receipts):
        if str(r.get("receipt_hash", "")).startswith(prefix):
            return r
    return None


def _make_cli_handler(ctx: Any):
    def handler(args) -> int:
        action = getattr(args, "action", "list")
        target = getattr(args, "target", "")
        if action == "list":
            receipts = list(ctx.state.get("receipts", None) or [])
            if not receipts:
                print("No receipts yet.")
                return 0
            for r in receipts[-20:]:
                c = r.get("contract", {})
                print(f"{r.get('receipt_hash', '')[:12]}  {r.get('created_at', '')}  {len(c.get('checks', []))} checks  {c.get('title', '')}")
            return 0
        if action in ("show", "export"):
            receipt = _find_receipt(ctx, target)
            if not receipt:
                print("No such receipt.")
                return 1
            text = json.dumps({k: v for k, v in receipt.items() if k != "stale"}, indent=2, ensure_ascii=False)
            out = getattr(args, "out", "")
            if action == "export" and out:
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
                print(f"Wrote {out}")
            else:
                print(text)
            return 0
        if action == "verify":
            if not target:
                print("Usage: hermes contract verify receipt.json [--script]")
                return 1
            try:
                with open(target, encoding="utf-8") as fh:
                    receipt = json.load(fh)
            except (OSError, ValueError) as exc:
                print(f"Cannot read {target}: {exc}")
                return 1
            if getattr(args, "script", False):
                print(model.rerun_script(receipt), end="")
                return 0
            ok, problems = model.verify_receipt(receipt)
            c = receipt.get("contract", {})
            print(f"Receipt {receipt.get('receipt_hash', '')[:16]} for “{c.get('title', '')}”: {'OK' if ok else 'NOT VALID'}")
            for p in problems:
                print(f"  ✗ {p}")
            ws = receipt.get("workspace") or {}
            if ws.get("tree"):
                print(f"  code tree: {ws['tree']}  (compare with: git rev-parse HEAD^{{tree}})")
            print(f"  checks: {len(c.get('checks', []))}, approved by {c.get('approved_by')}; "
                  "run with --script | bash to re-run them")
            return 0 if ok else 2
        return 1
    return handler


def register(ctx: Any) -> None:
    plugin = ContractPlugin(ctx)
    ctx.register_hook("post_tool_call", plugin.on_post_tool_call)
    ctx.register_hook("pre_tool_call", plugin.on_pre_tool_call)
    ctx.register_hook("pre_llm_call", plugin.on_pre_llm_call)
    ctx.register_hook("pre_verify", plugin.on_pre_verify)
    ctx.register_hook("transform_llm_output", plugin.on_transform_llm_output)
    ctx.register_hook("on_session_delete", plugin.on_session_delete)
    ctx.register_hook("subagent_start", plugin.on_subagent_start)
    ctx.register_tool(name="contract_propose", toolset=TOOLSET, schema=schemas.CONTRACT_PROPOSE,
                      handler=plugin.tool_propose, emoji="📜")
    ctx.register_tool(name="contract_status", toolset=TOOLSET, schema=schemas.CONTRACT_STATUS,
                      handler=plugin.tool_status, emoji="📜")
    ctx.register_tool(name="contract_run", toolset=TOOLSET, schema=schemas.CONTRACT_RUN,
                      handler=plugin.tool_run, emoji="📜")
    ctx.register_tool(name="vouch_evidence", toolset=TOOLSET, schema=schemas.VOUCH_EVIDENCE,
                      handler=plugin.vouch.tool_evidence, emoji="🧾")
    ctx.register_tool(name="vouch_check", toolset=TOOLSET, schema=schemas.VOUCH_CHECK,
                      handler=plugin.vouch.tool_check, emoji="🧾")
    ctx.register_command("contract", handler=plugin.command,
                         description="Show, approve or reset the definition-of-done contract",
                         args_hint="[approve|add|lock|unlock|receipt|reset]")
    ctx.register_command("vouch", handler=plugin.vouch.command,
                         description="Show which claims in the last reply the tool record supports",
                         args_hint="[evidence|stats]")
    ctx.register_cli_command(name="contract", help="Contract receipts: list, show, export, verify",
                             setup_fn=_cli_setup, handler_fn=_make_cli_handler(ctx),
                             description="List, export and verify contract receipts.")
    ctx.register_cli_command(name="vouch", help="Vouch claim-check totals",
                             setup_fn=_vouch_cli_setup, handler_fn=_vouch_cli_handler(ctx),
                             description="Show how often the agent's claims matched the tool record.")
