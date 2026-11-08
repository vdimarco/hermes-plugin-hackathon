#!/usr/bin/env python3
# Smoke test inside a real Hermes install (no LLM needed).
# Install and enable contract and multiverse in HERMES_HOME first, then run from any folder:
#   HERMES_HOME=~/.hermes <hermes venv>/bin/python scripts/smoke-hermes.py
# It makes a throwaway git repo in a temp folder and drives the plugins through
# model_tools.handle_function_call, the plugin hooks and the plugin slash commands.
import json, os, subprocess, sys, tempfile, pathlib, traceback

CART = '''def apply_discount(total, percent):
    discounted = total * (1 - percent / 100)
    discounted = discounted * (1 - percent / 100)
    return round(discounted, 2)
'''
FIXED = CART.replace("    discounted = discounted * (1 - percent / 100)\n", "")
CHECK = 'python3 -c "import cart; assert cart.apply_discount(100, 10) == 90, cart.apply_discount(100, 10)"'

repo = pathlib.Path(tempfile.mkdtemp(prefix="shop-")).resolve() / "shop"
repo.mkdir()
def git(*a): subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
git("init", "-q"); git("config", "user.email", "t@example.com"); git("config", "user.name", "t")
(repo / "cart.py").write_text(CART); (repo / ".gitignore").write_text("__pycache__/\n")
(repo / "tests").mkdir(); (repo / "tests" / "test_cart.py").write_text("from cart import apply_discount\n\ndef test_ten():\n    assert apply_discount(100, 10) == 90\n")
git("add", "-A"); git("commit", "-qm", "init")
os.chdir(repo)
os.environ["TERMINAL_CWD"] = str(repo)

from hermes_cli.plugins import discover_plugins, get_plugin_manager, get_pre_verify_continue_message, get_plugin_command_handler
from hermes_cli.lifecycle import invoke_hook
from hermes_cli.plugin_command_dispatch import invoke_plugin_command
from model_tools import handle_function_call

results = []
def step(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  | {detail}" if detail else ""))

def call(tool, args, sid="s1", tid="t1"):
    out = handle_function_call(tool, args, task_id=tid, session_id=sid)
    try:
        return json.loads(out)
    except Exception:
        return {"_raw": out}

discover_plugins(force=True)
from tools.registry import registry
names = set(registry.get_all_tool_names())
step("plugin tools registered", {"vouch_evidence", "contract_run", "multiverse_open"} <= names)

# ---- Contract --------------------------------------------------------------
r = call("contract_propose", {"title": "Fix double discount", "checks": [{"name": "discount once", "command": CHECK}],
                              "locked_paths": ["tests/test_cart.py"]})
step("contract_propose", r.get("status") == "proposed", str(r)[:160])
handler = get_plugin_command_handler("contract")
out = invoke_plugin_command(handler, "approve", session_id="s1")
step("/contract approve", "approved" in str(out).lower(), str(out).splitlines()[0][:120] if out else "")
r = call("write_file", {"path": "tests/test_cart.py", "content": "def test_ten():\n    assert True\n"})
step("lock blocks write to locked test", "Contract lock" in json.dumps(r), json.dumps(r)[:160])
step("locked test file unchanged", "assert apply_discount" in (repo / "tests" / "test_cart.py").read_text())
r = call("terminal", {"command": f"sed -i 's/90/81/' tests/test_cart.py"})
step("lock blocks sed -i on locked test", "Contract lock" in json.dumps(r), json.dumps(r)[:160])
r = call("terminal", {"command": CHECK})
step("failing check runs in real terminal", r.get("exit_code") not in (0, None), f"exit={r.get('exit_code')}")
msg = get_pre_verify_continue_message(session_id="s1", platform="cli", model="m", coding=True, attempt=0,
                                      final_response="Done. All tests pass.", changed_paths=["cart.py"])
step("pre_verify gate holds the finish", bool(msg) and ("[Contract]" in msg or "[Vouch]" in msg), (msg or "")[:140])
call("read_file", {"path": "cart.py"})
r = call("write_file", {"path": "cart.py", "content": FIXED})
step("non-locked write allowed", "Contract lock" not in json.dumps(r) and (repo / "cart.py").read_text() == FIXED)
r = call("contract_run", {})
step("contract_run via real terminal -> met", r.get("status") == "met", str(r)[:200])
msg = get_pre_verify_continue_message(session_id="s1", platform="cli", model="m", coding=True, attempt=0,
                                      final_response="Fixed. The check passes and I pushed to origin.", changed_paths=["cart.py"])
step("contract met: claim check sends a false push back", bool(msg) and "[Vouch]" in msg and "push" in msg, (msg or "")[:140])
tree = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], capture_output=True, text=True).stdout.strip()
out = invoke_plugin_command(handler, "receipt", session_id="s1")
step("/contract receipt has the PR block", "Contract met" in str(out) and "```json" in str(out))

# ---- Vouch, the claim check inside Contract --------------------------------
reps = invoke_hook("transform_llm_output", response_text="Fixed cart.py. The check passes and I pushed to origin.",
                   session_id="s1", model="m", platform="cli", turn_id="turn-1")
footer = next((x for x in reps if isinstance(x, str)), "")
step("one footer: vouch flags the unbacked push", "changes pushed" in footer and "no git push" in footer, "")
step("one footer: it also carries the contract line", "Contract met" in footer, footer.splitlines()[-1][:120] if footer else "no rewrite")
step("only one plugin rewrote the reply", sum(isinstance(x, str) for x in reps) == 1, f"rewrites={sum(isinstance(x, str) for x in reps)}")
out = invoke_plugin_command(get_plugin_command_handler("vouch"), "", session_id="s1")
step("/vouch lists the verdicts", "contradicted" in str(out) or "unverified" in str(out), str(out).splitlines()[0][:120] if out else "")
r = call("vouch_evidence", {"kind": "edits"})
step("vouch_evidence sees the real write", any("cart.py" in json.dumps(e) for e in r.get("events", [])), str(r)[:140])

# ---- Multiverse ------------------------------------------------------------
r = call("multiverse_open", {"goal": "Fix the double discount", "checks": [CHECK], "count": 2})
run_id = r.get("run")
step("multiverse_open makes real worktrees", bool(run_id) and all(pathlib.Path(p).is_dir() for p in r.get("universes", {}).values()), str(r)[:200])
if run_id:
    tasks = r["delegate_task"]["tasks"]
    wt = r["universes"]
    git("stash", "-q") if False else None
    invoke_hook("subagent_start", parent_session_id="s1", parent_turn_id="x", parent_subagent_id=None,
                child_session_id="child-u1", child_subagent_id="sa1", child_role="leaf", child_goal=tasks[0]["goal"])
    before = (repo / "cart.py").read_text()
    call("read_file", {"path": "cart.py"}, sid="child-u1", tid="child-u1")
    rr = call("write_file", {"path": "cart.py", "content": "# universe u1 edit\n" + FIXED}, sid="child-u1", tid="child-u1")
    in_wt = (pathlib.Path(wt["u1"]) / "cart.py").read_text()
    step("subagent write pinned into its worktree", in_wt.startswith("# universe u1") and (repo / "cart.py").read_text() == before, json.dumps(rr)[:120])
    rr = call("terminal", {"command": "pwd"}, sid="child-u1", tid="child-u1")
    step("subagent terminal pinned to its worktree", wt["u1"] in json.dumps(rr), json.dumps(rr)[:160])
    rr = call("write_file", {"path": "tests/test_cart.py", "content": "x"}, sid="child-u1", tid="child-u1")
    step("contract lock also applies to the subagent", "Contract lock" in json.dumps(rr), json.dumps(rr)[:140])
    invoke_hook("subagent_stop", parent_session_id="s1", child_session_id="child-u1", child_role="leaf",
                child_summary="fixed it", child_status="completed", tool_call_history=[], duration_ms=1000)
    rr = call("multiverse_judge", {"run": run_id})
    step("multiverse_judge runs checks in each worktree", "scoreboard" in rr, (rr.get("scoreboard") or str(rr))[:200].replace("\n", " / "))
    step("winner is the universe that fixed it", rr.get("winner") == "u1", f"winner={rr.get('winner')}")
    rr = call("multiverse_adopt", {"run": run_id})
    step("multiverse_adopt applies the winner", rr.get("adopted") == "u1" and (repo / "cart.py").read_text().startswith("# universe u1"), str(rr)[:200])
    gone = all(not pathlib.Path(p).exists() for p in wt.values())
    step("worktrees removed after adopt", gone)

print()
print(f"{sum(ok for _, ok in results)}/{len(results)} passed")
