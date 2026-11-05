"""Multiverse: try a coding task several ways at once, keep the one that passes.

multiverse_race makes one git worktree per attempt from the same base, starts one
subagent per worktree through delegate_task, pins each subagent to its worktree,
runs the same checks in every worktree and returns a scoreboard. multiverse_adopt
applies the winner's diff to the real working tree.

* subagent_start / subagent_stop bind each subagent to its universe by a marker in
  its goal, and record its summary.
* pre_tool_call rewrites the subagent's terminal workdir and file paths into its
  worktree, and blocks access to the other universes.
* An optional delegation provider (delegation.backend: multiverse) gives each
  universe its own model, so a race can compare models as well as approaches.

All git commands run through the Hermes terminal tool, so approvals and the
configured terminal backend apply. No network calls, no credentials.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

from . import runs, schemas

logger = logging.getLogger(__name__)

VERSION = "1.0.0"
TOOLSET = "multiverse"
MAX_RUNS = 16


def _parse(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    try:
        data = json.loads(result) if isinstance(result, str) else {}
    except (TypeError, ValueError):
        return {"output": str(result)}
    return data if isinstance(data, dict) else {"output": str(result)}


class Multiverse:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._lock = threading.RLock()
        self.runs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.bindings: Dict[str, Tuple[str, str]] = {}   # child session/subagent id -> (run, label)
        self._loaded = False

    # -- persistence ----------------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            for run in (self.ctx.state.get("runs", None) or []):
                if isinstance(run, dict) and run.get("id"):
                    self.runs[run["id"]] = run
        except Exception:
            logger.debug("multiverse: cannot read state", exc_info=True)

    def _save(self) -> None:
        try:
            while len(self.runs) > MAX_RUNS:
                self.runs.popitem(last=False)
            self.ctx.state.set("runs", list(self.runs.values()))
        except Exception:
            logger.debug("multiverse: cannot write state", exc_info=True)

    def get_run(self, run_id: str = "") -> Optional[Dict[str, Any]]:
        with self._lock:
            self._load()
            if run_id:
                return self.runs.get(run_id)
            live = [r for r in self.runs.values() if r.get("status") not in ("closed", "adopted")]
            return live[-1] if live else None

    def universe(self, run_id: str, label: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        run = self.get_run(run_id)
        if not run or label not in run["universes"]:
            return None
        return run, run["universes"][label]

    # -- terminal -------------------------------------------------------------
    def _sh(self, command: str, ids: Dict[str, Any], workdir: str = "") -> Tuple[int, str]:
        call = {"command": command}
        if workdir:
            call["workdir"] = workdir
        raw = self.ctx.dispatch_tool("terminal", call, **{k: v for k, v in ids.items() if v})
        data = _parse(raw)
        code = data.get("exit_code")
        output = str(data.get("output") or "")
        if data.get("error"):
            output += f"\n{data['error']}"
        return (code if isinstance(code, int) else (1 if data.get("error") else 0)), output

    # -- hooks ----------------------------------------------------------------
    def on_subagent_start(self, child_goal: str = "", child_session_id: Optional[str] = None,
                          child_subagent_id: Optional[str] = None, **kwargs: Any) -> None:
        try:
            found = runs.parse_marker(child_goal)
            if not found or not self.universe(*found):
                return
            run, u = self.universe(*found)
            with self._lock:
                for ident in (child_session_id, child_subagent_id):
                    if ident:
                        self.bindings[str(ident)] = found
                        if str(ident) not in u["children"]:
                            u["children"].append(str(ident))
                u["status"] = "running"
                self._save()
        except Exception:
            logger.debug("multiverse: subagent_start failed", exc_info=True)

    def on_subagent_stop(self, child_session_id: Optional[str] = None, child_summary: str = "",
                         child_status: str = "", duration_ms: int = 0, **kwargs: Any) -> None:
        try:
            found = self.bindings.get(str(child_session_id or ""))
            if not found or not self.universe(*found):
                return
            run, u = self.universe(*found)
            with self._lock:
                status = str(child_status or "").lower()
                u["status"] = "failed" if status in ("failed", "error", "interrupted", "timeout") else "done"
                u["summary"] = str(child_summary or "")[-1500:]
                u["duration_ms"] = int(duration_ms or 0)
                self._save()
        except Exception:
            logger.debug("multiverse: subagent_stop failed", exc_info=True)

    def on_pre_tool_call(self, tool_name: str = "", args: Any = None, session_id: Optional[str] = None,
                         task_id: Optional[str] = None, **kwargs: Any) -> Optional[Dict[str, Any]]:
        found = None
        for ident in (session_id, task_id):
            if ident and str(ident) in self.bindings:
                found = self.bindings[str(ident)]
                break
        if not found:
            return None
        try:
            pair = self.universe(*found)
            if not pair:
                return None
            run, u = pair
            if run.get("status") in ("closed", "adopted"):
                return {"action": "block", "message": "[Multiverse] This run is closed; its worktree is gone."}
            return runs.rewrite(tool_name, args if isinstance(args, dict) else {}, run, u)
        except Exception:
            logger.debug("multiverse: pre_tool_call failed", exc_info=True)
            return {"action": "block", "message": "[Multiverse] Could not pin this call to its worktree, so it was stopped."}

    # -- tools ----------------------------------------------------------------
    def _open(self, args: Dict[str, Any], ids: Dict[str, Any]) -> Dict[str, Any]:
        attempts = args.get("attempts") or args.get("count") or 3
        universes = runs.plan_universes(attempts)
        code, out = self._sh(runs.INFO_CMD, ids, workdir=str(args.get("repo") or ""))
        info = runs.parse_info(out)
        run = runs.new_run(str(args.get("goal") or ""), list(args.get("checks") or []), universes,
                           info.get("root", ""), info.get("cwd", ""), info.get("head", ""), info.get("wip", ""))
        if not run["base"]:
            raise runs.RunError("the repository has no commit yet; commit once before a race")
        code, out = self._sh(runs.create_cmd(run), ids)
        if code != 0:
            raise runs.RunError(f"could not create the worktrees (exit {code}): {out[-400:]}")
        with self._lock:
            self._load()
            self.runs[run["id"]] = run
            self._save()
        return run

    def tool_open(self, args: Dict[str, Any], task_id: Optional[str] = None, session_id: Optional[str] = None,
                  **kwargs: Any) -> str:
        try:
            run = self._open(args or {}, {"task_id": task_id, "session_id": session_id})
            return json.dumps({
                "run": run["id"],
                "universes": {k: u["path"] for k, u in run["universes"].items()},
                "next": "Call delegate_task with exactly these tasks (keep each goal's [multiverse:...] marker), "
                        "then call multiverse_judge.",
                "delegate_task": {"tasks": runs.delegate_tasks(run)},
            })
        except runs.RunError as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:
            return json.dumps({"error": f"multiverse_open failed: {exc}"})

    def tool_race(self, args: Dict[str, Any], task_id: Optional[str] = None, session_id: Optional[str] = None,
                  parent_agent: Any = None, **kwargs: Any) -> str:
        ids = {"task_id": task_id, "session_id": session_id}
        try:
            run = self._open(args or {}, ids)
        except runs.RunError as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:
            return json.dumps({"error": f"multiverse_race failed: {exc}"})
        tasks = runs.delegate_tasks(run)
        try:
            extra = dict(ids)
            if parent_agent is not None:
                extra["parent_agent"] = parent_agent
            raw = self.ctx.dispatch_tool("delegate_task", {"tasks": tasks}, **{k: v for k, v in extra.items() if v is not None})
            data = _parse(raw)
            if data.get("error"):
                raise RuntimeError(str(data["error"]))
        except Exception as exc:
            return json.dumps({
                "run": run["id"],
                "error": f"the worktrees are ready, but the plugin could not start the subagents itself ({exc}).",
                "next": "Call delegate_task with these tasks, then multiverse_judge.",
                "delegate_task": {"tasks": tasks},
            })
        return self._judge(run, ids)

    def _judge(self, run: Dict[str, Any], ids: Dict[str, Any]) -> str:
        for u in run["universes"].values():
            results = []
            for check in run["checks"]:
                code, out = self._sh(check, ids, workdir=runs.workdir(run, u))
                results.append({"command": check, "exit_code": code, "passed": code == 0,
                                "output_tail": out[-300:] if code != 0 else ""})
            u["results"] = results
            code, out = self._sh(runs.diff_cmd(run, u), ids)
            u["diff"] = runs.parse_diff(out)
            if u["status"] in ("open", "running"):
                u["status"] = "done"
        runs.rank(run)
        with self._lock:
            self._save()
        board = runs.scoreboard(run)
        return json.dumps({
            "run": run["id"],
            "winner": run["winner"],
            "scoreboard": board,
            "universes": {k: {"checks": f"{u['score']['passed']}/{u['score']['total']}", "diff": {
                x: u["diff"].get(x) for x in ("files", "insertions", "deletions")}, "summary": u.get("summary", "")[-600:],
                "failures": [r["output_tail"] for r in u["results"] if not r["passed"]][:2]}
                for k, u in run["universes"].items()},
            "next": ("Show the scoreboard to the user, then call multiverse_adopt." if run["winner"] else
                     "No clear winner. Show the scoreboard and ask the user which universe to adopt, or close the run."),
        })

    def tool_judge(self, args: Dict[str, Any], task_id: Optional[str] = None, session_id: Optional[str] = None,
                   **kwargs: Any) -> str:
        try:
            run = self.get_run(str((args or {}).get("run") or ""))
            if not run:
                return json.dumps({"error": "No open multiverse run."})
            return self._judge(run, {"task_id": task_id, "session_id": session_id})
        except Exception as exc:
            return json.dumps({"error": f"multiverse_judge failed: {exc}"})

    def tool_adopt(self, args: Dict[str, Any], task_id: Optional[str] = None, session_id: Optional[str] = None,
                   **kwargs: Any) -> str:
        args = args or {}
        ids = {"task_id": task_id, "session_id": session_id}
        try:
            run = self.get_run(str(args.get("run") or ""))
            if not run:
                return json.dumps({"error": "No open multiverse run."})
            label = str(args.get("universe") or run.get("winner") or "")
            if label not in run["universes"]:
                return json.dumps({"error": f"Name the universe to adopt ({', '.join(run['universes'])}); there is no judged winner."})
            code, out = self._sh(runs.adopt_cmd(run, label), ids)
            if code != 0:
                return json.dumps({"error": f"git apply failed (exit {code}); the working tree may have changed since the race. "
                                            f"The patch is at {runs.patch_path(run, label)}. Output: {out[-400:]}"})
            run["adopted"] = label
            if not args.get("keep_worktrees"):
                self._sh(runs.close_cmd(run), ids)
                run["status"] = "adopted"
            with self._lock:
                self._save()
            return json.dumps({"adopted": label, "changed": out.strip().splitlines()[-40:],
                               "next": "Run the checks once more in the real tree, then report to the user."})
        except Exception as exc:
            return json.dumps({"error": f"multiverse_adopt failed: {exc}"})

    def tool_close(self, args: Dict[str, Any], task_id: Optional[str] = None, session_id: Optional[str] = None,
                   **kwargs: Any) -> str:
        try:
            run = self.get_run(str((args or {}).get("run") or ""))
            if not run:
                return json.dumps({"error": "No open multiverse run."})
            self._sh(runs.close_cmd(run), {"task_id": task_id, "session_id": session_id})
            run["status"] = "closed"
            with self._lock:
                self._save()
            return json.dumps({"closed": run["id"]})
        except Exception as exc:
            return json.dumps({"error": f"multiverse_close failed: {exc}"})

    # -- slash command and CLI ------------------------------------------------
    def command(self, raw_args: str = "", **kwargs: Any) -> str:
        sub, _, rest = (raw_args or "").strip().partition(" ")
        if sub in ("", "list"):
            with self._lock:
                self._load()
                items = list(self.runs.values())[-8:]
            if not items:
                return "No multiverse runs yet. Ask the agent to race a task (multiverse_race)."
            return "\n".join(f"{r['id']}  {r['status']:<8} {len(r['universes'])} universes  winner: {r.get('winner') or '-'}  {r['goal'][:60]}"
                             for r in items)
        run = self.get_run(rest.strip() if sub in ("show", "board") else sub)
        if not run:
            return "No such run. /multiverse lists them."
        return runs.scoreboard(run) + "\n\n" + "\n".join(f"- {u['label']}: {u['path']} ({u['status']})" for u in run["universes"].values())


def format_runs_cli(ctx: Any) -> str:
    stored = list(ctx.state.get("runs", None) or [])
    if not stored:
        return "No multiverse runs."
    lines = []
    for r in stored:
        lines.append(f"{r['id']}  {r['status']:<8} winner={r.get('winner') or '-'}  {r['goal'][:60]}")
        if r["status"] not in ("closed", "adopted"):
            lines.append(f"    clean up: {runs.close_cmd(r)}")
    return "\n".join(lines)


def _cli_setup(parser) -> None:
    parser.add_argument("action", nargs="?", default="list", choices=["list"])


def _make_cli_handler(ctx: Any):
    def handler(args) -> int:
        print(format_runs_cli(ctx))
        return 0
    return handler


def _make_provider(plugin: Multiverse):
    """The optional delegation backend. Built lazily so the plugin loads without it."""
    from agent.delegation_provider import DelegationProvider

    class MultiverseDelegation(DelegationProvider):
        @property
        def name(self) -> str:
            return "multiverse"

        @property
        def display_name(self) -> str:
            return "Multiverse"

        @property
        def summary(self) -> str:
            return "Like default, plus a per-universe model for multiverse_race attempts."

        def route_task(self, task, config):
            base = {k: v for k, v in dict(config or {}).items() if k != "service_tier"}
            found = runs.parse_marker(str((task or {}).get("goal") or ""))
            pair = plugin.universe(*found) if found else None
            if not pair:
                return base
            _, u = pair
            route = dict(base)
            if u.get("model"):
                route = {"model": u["model"]}
                if u.get("provider"):
                    route["provider"] = u["provider"]
            route["label"] = f"{u['label']} · {u.get('model') or 'default model'}"
            return route

    return MultiverseDelegation()


def register(ctx: Any) -> None:
    plugin = Multiverse(ctx)
    ctx.register_hook("subagent_start", plugin.on_subagent_start)
    ctx.register_hook("subagent_stop", plugin.on_subagent_stop)
    ctx.register_hook("pre_tool_call", plugin.on_pre_tool_call)
    for name, schema, handler in (
        ("multiverse_race", schemas.MULTIVERSE_RACE, plugin.tool_race),
        ("multiverse_open", schemas.MULTIVERSE_OPEN, plugin.tool_open),
        ("multiverse_judge", schemas.MULTIVERSE_JUDGE, plugin.tool_judge),
        ("multiverse_adopt", schemas.MULTIVERSE_ADOPT, plugin.tool_adopt),
        ("multiverse_close", schemas.MULTIVERSE_CLOSE, plugin.tool_close),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler, emoji="🌌")
    ctx.register_command("multiverse", handler=plugin.command,
                         description="List multiverse runs or show a scoreboard", args_hint="[run id]")
    ctx.register_cli_command(name="multiverse", help="List multiverse runs and cleanup commands",
                             setup_fn=_cli_setup, handler_fn=_make_cli_handler(ctx))
    try:
        ctx.register_delegation_provider(_make_provider(plugin))
    except Exception:
        logger.debug("multiverse: delegation provider not registered", exc_info=True)
