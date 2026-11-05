"""Contract model: checks, locks, results, status and receipts.

A contract is a short list of shell checks that define "done" for one task,
plus a list of locked paths that the agent may not change. The user approves
the contract before it is enforced. Each check result comes from a real
terminal run. A receipt is a JSON record of the passing runs with a hash
chain, so that a reviewer can see if someone edited it.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import shlex
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "hermes-contract-receipt/1"
MAX_CHECKS = 12
OUTPUT_KEEP = 20000

DEFAULT_TEST_LOCKS = [
    "tests/*", "test/*", "spec/*", "__tests__/*",
    "test_*.py", "*_test.py", "*_test.go", "*.test.*", "*.spec.*", "conftest.py",
]

PROPOSED, ACTIVE, MET, CANCELLED = "proposed", "active", "met", "cancelled"
PENDING, PASS, FAIL, STALE = "pending", "pass", "fail", "stale"
ICONS = {PENDING: "⏳", PASS: "✅", FAIL: "❌", STALE: "♻️"}


class ContractError(ValueError):
    """A contract that cannot be accepted. The message is shown to the model."""


# ---------------------------------------------------------------------------
# hashing
# ---------------------------------------------------------------------------

def canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def norm_cmd(command: str) -> str:
    return " ".join((command or "").split())


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------

def _clean_check(raw: Any, index: int) -> Dict[str, Any]:
    if isinstance(raw, str):
        raw = {"command": raw}
    if not isinstance(raw, dict):
        raise ContractError(f"check {index + 1} must be an object with a command")
    command = norm_cmd(str(raw.get("command") or ""))
    if not command:
        raise ContractError(f"check {index + 1} has no command")
    if len(command) > 500:
        raise ContractError(f"check {index + 1}: command is longer than 500 characters")
    try:
        expect_exit = int(raw.get("expect_exit", 0))
    except (TypeError, ValueError):
        raise ContractError(f"check {index + 1}: expect_exit must be an integer")
    check = {
        "id": f"c{index + 1}",
        "name": str(raw.get("name") or command)[:80],
        "command": command,
        "expect_exit": expect_exit,
    }
    for key in ("expect_output", "forbid_output"):
        pattern = raw.get(key)
        if pattern:
            try:
                re.compile(str(pattern))
            except re.error as exc:
                raise ContractError(f"check {index + 1}: {key} is not a valid regex: {exc}")
            check[key] = str(pattern)
    if raw.get("workdir"):
        check["workdir"] = str(raw["workdir"])
    return check


def paths_in_command(command: str) -> List[str]:
    """File paths named in a check command, for example tests/test_cart.py."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    out = []
    for tok in tokens:
        if tok.startswith("-") or "=" in tok or "://" in tok:
            continue
        tok = tok.split("::", 1)[0]  # pytest node ids
        if re.search(r"\.[A-Za-z0-9]{1,6}$", tok) or ("/" in tok and not tok.startswith("/dev/")):
            if tok not in out:
                out.append(tok.rstrip("/"))
    return out


def new_contract(title: str, checks: List[Any], locked: Optional[List[str]] = None,
                 lock_tests: bool = True, approved: bool = False, author: str = "agent") -> Dict[str, Any]:
    if not checks:
        raise ContractError("a contract needs at least one check")
    if len(checks) > MAX_CHECKS:
        raise ContractError(f"a contract can have at most {MAX_CHECKS} checks")
    clean = [_clean_check(c, i) for i, c in enumerate(checks)]
    locks: List[str] = []
    for item in list(locked or []) + [p for c in clean for p in paths_in_command(c["command"])]:
        item = str(item).strip()
        if item and item not in locks:
            locks.append(item)
    if lock_tests:
        locks += [g for g in DEFAULT_TEST_LOCKS if g not in locks]
    now = int(time.time())
    contract = {
        "id": uuid.uuid4().hex[:10],
        "title": (title or "Untitled task").strip()[:120],
        "status": ACTIVE if approved else PROPOSED,
        "author": author,
        "created_at": now,
        "approved_at": now if approved else None,
        "approved_by": "user" if approved and author == "user" else ("auto" if approved else None),
        "checks": clean,
        "locked": locks,
        "results": {},          # check id -> latest result
        "activated_seq": 0,
        "receipt": None,
    }
    contract["hash"] = contract_hash(contract)
    return contract


def contract_hash(contract: Dict[str, Any]) -> str:
    return sha256(canonical({"title": contract["title"], "checks": contract["checks"], "locked": contract["locked"]}))


# ---------------------------------------------------------------------------
# locks
# ---------------------------------------------------------------------------

def _pattern(glob: str) -> str:
    glob = glob.strip().replace("\\", "/")
    while glob.startswith("./"):
        glob = glob[2:]
    while glob.startswith("**/"):
        glob = glob[3:]
    return glob.rstrip("/")


def _suffixes(path: str) -> List[str]:
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
    return ["/".join(parts[i:]) for i in range(len(parts))]


def path_locked(path: str, locks: List[str]) -> Optional[str]:
    """Return the lock that covers *path*, or None."""
    if not path:
        return None
    suffixes = _suffixes(path)
    for lock in locks:
        pat = _pattern(lock)
        if not pat:
            continue
        for s in suffixes:
            if fnmatch.fnmatchcase(s, pat) or s == pat or s.startswith(pat + "/"):
                return lock
    return None


_SPLIT_OPS = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")
_REDIRECT = re.compile(r"(?:^|\s)\d*>>?\s*([^\s;&|]+)")


def command_write_targets(command: str) -> List[str]:
    """Paths a shell command would change. A heuristic, tuned to avoid false alarms."""
    targets: List[str] = [m.group(1) for m in _REDIRECT.finditer(command or "")]
    for segment in _SPLIT_OPS.split(command or ""):
        segment = _REDIRECT.sub(" ", segment)
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        # Skip "VAR=1", "env", "time" and similar prefixes.
        while words and (("=" in words[0] and not words[0].startswith("-")) or words[0] in ("env", "command", "time", "nice", "nohup")):
            words = words[1:]
        if not words:
            continue
        cmd, args = words[0].rsplit("/", 1)[-1], words[1:]
        plain = [a for a in args if not a.startswith("-")]
        if cmd in ("rm", "rmdir", "touch", "truncate", "tee", "chmod", "unlink", "shred", "mv"):
            targets += plain
        elif cmd in ("cp", "ln", "install", "rsync") and plain:
            targets.append(plain[-1])
        elif cmd in ("sed", "perl", "gsed") and any(a.startswith("-i") or a.startswith("-pi") or a == "-p" for a in args):
            targets += plain[1:]  # the first plain argument is the script
        elif cmd == "git" and args:
            sub = args[0]
            if sub in ("checkout", "restore", "rm", "mv", "apply", "reset", "stash"):
                rest = [a for a in args[1:] if not a.startswith("-") and a != "--"]
                targets += rest or ["."]
        elif cmd in ("dd",):
            targets += [a[3:] for a in args if a.startswith("of=")]
    return [t.strip("'\"") for t in targets if t and t not in ("/dev/null", "&1", "&2")]


def lock_violation(tool_name: str, args: Dict[str, Any], locks: List[str]) -> Optional[Tuple[str, str]]:
    """Return (path, lock) when this tool call would change a locked path."""
    args = args if isinstance(args, dict) else {}
    paths: List[str] = []
    if tool_name in ("write_file", "patch"):
        if isinstance(args.get("path"), str):
            paths.append(args["path"])
        body = args.get("patch")
        if isinstance(body, str):
            paths += re.findall(r"^\*\*\*\s+(?:Update|Add|Delete|Move to)\s+File:\s*(.+?)\s*$", body, re.MULTILINE)
    elif tool_name == "terminal":
        paths += command_write_targets(str(args.get("command") or ""))
    elif tool_name == "execute_code":
        code = str(args.get("code") or "")
        if re.search(r"""open\([^)]*['"][wax+]|\.write_text\(|\.write_bytes\(|os\.remove|\.unlink\(|shutil\.(?:move|copy|rmtree)""", code):
            literals = re.findall(r"""['"]([^'"\n]{2,200})['"]""", code)
            paths += [p for p in literals if "/" in p or "." in p]
    for p in paths:
        hit = path_locked(p, locks)
        if hit:
            return p, hit
    return None


# ---------------------------------------------------------------------------
# results and status
# ---------------------------------------------------------------------------

def match_check(contract: Dict[str, Any], command: str) -> List[Dict[str, Any]]:
    """Checks whose command is part of *command* (so "cd app && pytest -q" counts for "pytest -q")."""
    have = norm_cmd(command)
    out = []
    for check in contract.get("checks", []):
        want = check["command"]
        if have == want or re.search(r"(?:^|&&|;|\|\||\()\s*" + re.escape(want) + r"\s*(?:$|&&|;|\||\)|\d?>)", have):
            out.append(check)
    return out


def judge(check: Dict[str, Any], exit_code: Optional[int], output: str, background: bool = False,
          failure_in_output: bool = False) -> Tuple[bool, str]:
    """Decide pass or fail for one run, with a short reason."""
    if background:
        return False, "started in background, no exit code"
    if exit_code is None:
        return False, "no exit code"
    if exit_code != check.get("expect_exit", 0):
        return False, f"exit {exit_code}, expected {check.get('expect_exit', 0)}"
    if check.get("expect_exit", 0) == 0 and failure_in_output and not check.get("expect_output"):
        return False, "exit 0, but the output shows failures"
    if check.get("expect_output") and not re.search(check["expect_output"], output or "", re.MULTILINE):
        return False, f"output does not match /{check['expect_output']}/"
    if check.get("forbid_output") and re.search(check["forbid_output"], output or "", re.MULTILINE):
        return False, f"output matches forbidden /{check['forbid_output']}/"
    return True, f"exit {exit_code}"


def make_result(check: Dict[str, Any], seq: int, command: str, exit_code: Optional[int], output: str,
                passed: bool, reason: str, source: str) -> Dict[str, Any]:
    output = output or ""
    return {
        "check_id": check["id"], "seq": seq, "ts": int(time.time()), "command": norm_cmd(command),
        "exit_code": exit_code, "passed": passed, "reason": reason, "source": source,
        "output_sha256": sha256(output[-OUTPUT_KEEP:]), "output_tail": output[-400:],
    }


def check_states(contract: Dict[str, Any], last_write_seq: int) -> List[Tuple[Dict[str, Any], str, Optional[Dict[str, Any]]]]:
    out = []
    for check in contract.get("checks", []):
        result = contract.get("results", {}).get(check["id"])
        if not result:
            state = PENDING
        elif not result["passed"]:
            state = FAIL
        elif result["seq"] < last_write_seq:
            state = STALE
        else:
            state = PASS
        out.append((check, state, result))
    return out


def all_pass(contract: Dict[str, Any], last_write_seq: int) -> bool:
    states = check_states(contract, last_write_seq)
    return bool(states) and all(s == PASS for _, s, _ in states)


def summary_line(contract: Dict[str, Any], last_write_seq: int) -> str:
    states = check_states(contract, last_write_seq)
    passed = sum(1 for _, s, _ in states if s == PASS)
    return f"{passed}/{len(states)} checks pass"


def format_status(contract: Optional[Dict[str, Any]], last_write_seq: int) -> str:
    if not contract:
        return "No contract in this session. The agent can propose one with contract_propose, or you can add a check with /contract add <command>."
    lines = [f"Contract “{contract['title']}” ({contract['status']}, {summary_line(contract, last_write_seq)}) · id {contract['id']}"]
    for check, state, result in check_states(contract, last_write_seq):
        detail = ""
        if result:
            detail = f" · {result['reason']}"
            if state == STALE:
                detail += ", then files changed"
        lines.append(f"  {ICONS[state]} {check['id']} {check['name']}: `{check['command']}`{detail}")
    lines.append("  🔒 locked: " + (", ".join(contract["locked"][:12]) or "nothing"))
    if contract["status"] == PROPOSED:
        lines.append("  Not enforced yet. Run /contract approve to lock it, or /contract reset to drop it.")
    if contract.get("receipt"):
        lines.append(f"  🧾 receipt {contract['receipt']['receipt_hash'][:12]} (/contract receipt)")
    return "\n".join(lines)


def format_gate_message(contract: Dict[str, Any], last_write_seq: int) -> str:
    lines = [f"[Contract] The approved contract “{contract['title']}” is not met ({summary_line(contract, last_write_seq)}). You cannot finish yet:"]
    for check, state, result in check_states(contract, last_write_seq):
        if state == PASS:
            continue
        why = {PENDING: "has not run", FAIL: f"failed ({result['reason'] if result else ''})",
               STALE: "passed before your last edit, so it must run again"}[state]
        lines.append(f"- {check['id']} `{check['command']}` {why}")
    lines.append(
        "Fix the code until every check passes, then call contract_run. "
        "Do not change the checks or the locked files: " + ", ".join(contract["locked"][:8]) + ". "
        "If the contract itself is wrong, say so and ask the user to change it with /contract reset."
    )
    return "\n".join(lines)


def format_brief(contract: Dict[str, Any]) -> str:
    checks = "; ".join(f"{c['id']} `{c['command']}`" for c in contract["checks"])
    return (f"[Contract “{contract['title']}” is {contract['status']}] Definition of done: {checks}. "
            f"Locked (do not edit): {', '.join(contract['locked'][:10])}. "
            "Call contract_run to record results; the turn cannot finish until all checks pass.")


# ---------------------------------------------------------------------------
# receipts
# ---------------------------------------------------------------------------

def build_receipt(contract: Dict[str, Any], session_id: str, changed_paths: List[str],
                  workspace: Optional[Dict[str, Any]] = None, model: str = "", plugin_version: str = "") -> Dict[str, Any]:
    results = [contract["results"][c["id"]] for c in contract["checks"] if c["id"] in contract["results"]]
    chain, prev = [], sha256(contract["hash"])
    for r in results:
        prev = sha256(prev + canonical({k: r[k] for k in ("check_id", "command", "exit_code", "passed", "output_sha256", "ts")}))
        chain.append(prev)
    receipt = {
        "schema": SCHEMA,
        "contract": {k: contract[k] for k in ("id", "title", "checks", "locked", "hash", "approved_by", "approved_at")},
        "session": session_id,
        "model": model,
        "results": [{k: r[k] for k in ("check_id", "command", "exit_code", "passed", "reason", "source", "output_sha256", "output_tail", "ts")} for r in results],
        "changed_paths": changed_paths[:200],
        "workspace": workspace or {},
        "plugin": f"contract {plugin_version}".strip(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "chain": chain,
    }
    receipt["receipt_hash"] = sha256(canonical(receipt))
    return receipt


def verify_receipt(receipt: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Recompute every hash. Returns (ok, problems)."""
    problems: List[str] = []
    if receipt.get("schema") != SCHEMA:
        problems.append(f"unknown schema {receipt.get('schema')!r}")
    body = {k: v for k, v in receipt.items() if k != "receipt_hash"}
    if sha256(canonical(body)) != receipt.get("receipt_hash"):
        problems.append("receipt_hash does not match the content (the receipt was edited)")
    contract = receipt.get("contract") or {}
    if contract and contract_hash(contract) != contract.get("hash"):
        problems.append("contract hash does not match its checks and locks")
    prev = sha256(contract.get("hash", ""))
    results = receipt.get("results") or []
    chain = receipt.get("chain") or []
    if len(chain) != len(results):
        problems.append("chain length does not match the results")
    for i, r in enumerate(results):
        prev = sha256(prev + canonical({k: r.get(k) for k in ("check_id", "command", "exit_code", "passed", "output_sha256", "ts")}))
        if i < len(chain) and chain[i] != prev:
            problems.append(f"chain breaks at result {i + 1} ({r.get('check_id')})")
            break
    checks = {c["id"] for c in contract.get("checks", [])}
    passed = {r.get("check_id") for r in results if r.get("passed")}
    missing = sorted(checks - passed)
    if missing:
        problems.append("checks without a passing result: " + ", ".join(missing))
    return not problems, problems


def rerun_script(receipt: Dict[str, Any]) -> str:
    """A bash script that runs every check again and compares exit codes."""
    lines = ["#!/usr/bin/env bash", f"# Re-run the checks of contract receipt {receipt.get('receipt_hash', '')[:12]}",
             "# Review each command before you run this script.", "fail=0"]
    checks = {c["id"]: c for c in (receipt.get("contract") or {}).get("checks", [])}
    for cid, c in checks.items():
        cmd = c["command"]
        wd = c.get("workdir")
        run = f"(cd {shlex.quote(wd)} && {cmd})" if wd else f"({cmd})"
        lines += [
            f"echo '--- {cid}: {cmd.replace(chr(39), '')}'",
            f"{run}; code=$?",
            f"if [ \"$code\" -ne {int(c.get('expect_exit', 0))} ]; then echo 'FAIL {cid}: exit '$code; fail=1; else echo 'PASS {cid}'; fi",
        ]
    lines.append("exit $fail")
    return "\n".join(lines) + "\n"


def receipt_markdown(receipt: Dict[str, Any]) -> str:
    c = receipt["contract"]
    rows = ["| Check | Command | Result |", "|---|---|---|"]
    by_id = {r["check_id"]: r for r in receipt["results"]}
    for check in c["checks"]:
        r = by_id.get(check["id"])
        mark = "✅" if r and r["passed"] else "❌"
        rows.append(f"| {check['id']} {check['name']} | `{check['command']}` | {mark} {r['reason'] if r else 'not run'} |")
    ws = receipt.get("workspace") or {}
    tree = f" · tree `{ws['tree'][:12]}`" if ws.get("tree") else ""
    lines = [
        f"### ✅ Contract met: {c['title']}",
        "",
        *rows,
        "",
        f"Receipt `{receipt['receipt_hash'][:16]}` · contract `{c['hash'][:12]}` · approved by {c.get('approved_by') or 'nobody'}{tree} · "
        f"{len(receipt.get('changed_paths') or [])} files changed",
        "",
        "Verify: save the JSON below as `receipt.json`, then run `hermes contract verify receipt.json` "
        "(hashes) and `hermes contract verify receipt.json --script | bash` (re-runs the checks).",
        "",
        "<details><summary>receipt.json</summary>",
        "",
        "```json",
        json.dumps(receipt, indent=2, ensure_ascii=False),
        "```",
        "</details>",
    ]
    return "\n".join(lines)
