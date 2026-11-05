"""Multiverse runs: universes, worktree pinning, judging and the scoreboard.

A run is one task attempted in several universes. Each universe is a git
worktree made from the same base commit, worked on by one subagent. The judge
runs the same checks in every universe and ranks them.
"""

from __future__ import annotations

import os
import posixpath
import re
import shlex
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

MAX_UNIVERSES = 6
MARKER = re.compile(r"\[multiverse:([a-f0-9]{6,12})/([a-z0-9][a-z0-9-]{0,23})\]")
DEFAULT_APPROACHES = [
    "Take the most direct route to a correct fix.",
    "Avoid the first idea that comes to mind; find a different design and implement it.",
    "Make the smallest change that can possibly work, and touch as few files as you can.",
    "Start by writing a failing test that captures the problem, then fix it.",
    "Prefer clarity: refactor the code around the problem if that makes the fix obvious.",
    "Look for the root cause in the callers before you change the function itself.",
]
FILE_TOOLS = {"write_file", "patch", "read_file", "search_files"}
WRITE_TOOLS = {"write_file", "patch"}


class RunError(ValueError):
    """A request the plugin refuses. The message is shown to the model."""


def q(path: str) -> str:
    return shlex.quote(path)


def marker(run_id: str, label: str) -> str:
    return f"[multiverse:{run_id}/{label}]"


def parse_marker(text: str) -> Optional[Tuple[str, str]]:
    m = MARKER.search(text or "")
    return (m.group(1), m.group(2)) if m else None


def _label(raw: Any, index: int, used: set) -> str:
    label = re.sub(r"[^a-z0-9-]+", "-", str(raw or "").lower()).strip("-")[:24] or f"u{index + 1}"
    base, n = label, 2
    while label in used:
        label = f"{base[:20]}-{n}"
        n += 1
    used.add(label)
    return label


def plan_universes(attempts: Any) -> List[Dict[str, Any]]:
    """Turn the attempts argument (a count or a list) into universe specs."""
    if attempts is None or attempts == "":
        attempts = 3
    if isinstance(attempts, (int, float)) or (isinstance(attempts, str) and attempts.isdigit()):
        n = int(attempts)
        attempts = [{} for _ in range(n)]
    if not isinstance(attempts, list) or not attempts:
        raise RunError("attempts must be a number (2 to 6) or a list of attempts")
    if len(attempts) < 2 or len(attempts) > MAX_UNIVERSES:
        raise RunError(f"use 2 to {MAX_UNIVERSES} attempts")
    used: set = set()
    out = []
    for i, a in enumerate(attempts):
        a = a if isinstance(a, dict) else {"approach": str(a)}
        out.append({
            "label": _label(a.get("label") or (a.get("model", "").split("/")[-1] if a.get("model") else ""), i, used),
            "approach": str(a.get("approach") or DEFAULT_APPROACHES[i % len(DEFAULT_APPROACHES)])[:400],
            "model": str(a.get("model") or ""),
            "provider": str(a.get("provider") or ""),
        })
    return out


def new_run(goal: str, checks: List[str], universes: List[Dict[str, Any]], root: str, cwd: str,
            head: str, wip: str) -> Dict[str, Any]:
    if not goal or not goal.strip():
        raise RunError("goal is required")
    checks = [" ".join(str(c).split()) for c in (checks or []) if str(c).strip()]
    if not checks:
        raise RunError("give at least one check command; the judge needs a way to tell attempts apart")
    if not root.startswith("/"):
        raise RunError(f"could not find the git root (got {root!r}); run inside a git repository")
    run_id = uuid.uuid4().hex[:8]
    base_dir = posixpath.join(posixpath.dirname(root.rstrip("/")), f".{posixpath.basename(root.rstrip('/'))}.multiverse", run_id)
    rel = posixpath.relpath(cwd, root) if cwd and cwd.startswith(root) else "."
    run = {
        "id": run_id, "goal": goal.strip()[:4000], "checks": checks[:12], "root": root.rstrip("/"),
        "cwd_rel": rel, "base": wip or head, "base_kind": "working tree" if wip else "HEAD", "head": head,
        "base_dir": base_dir, "status": "open", "created_at": int(time.time()), "winner": None,
        "universes": {},
    }
    for u in universes:
        run["universes"][u["label"]] = dict(u, path=posixpath.join(base_dir, u["label"]), status="open",
                                            children=[], summary="", results=[], diff={}, score=None)
    return run


# ---------------------------------------------------------------------------
# shell commands (all run through the Hermes terminal tool)
# ---------------------------------------------------------------------------

INFO_CMD = (
    "printf 'root=%s\\n' \"$(git rev-parse --show-toplevel 2>/dev/null)\"; "
    "printf 'cwd=%s\\n' \"$(pwd -P)\"; "
    "printf 'head=%s\\n' \"$(git rev-parse HEAD 2>/dev/null)\"; "
    "printf 'wip=%s\\n' \"$(git stash create 2>/dev/null)\""
)


def parse_info(output: str) -> Dict[str, str]:
    info = {}
    for line in (output or "").splitlines():
        m = re.match(r"^(root|cwd|head|wip)=(.*)$", line.strip())
        if m:
            info[m.group(1)] = m.group(2).strip()
    return info


def create_cmd(run: Dict[str, Any]) -> str:
    parts = [f"mkdir -p {q(run['base_dir'])}"]
    for u in run["universes"].values():
        parts.append(f"git -C {q(run['root'])} worktree add --detach {q(u['path'])} {q(run['base'])}")
    return " && ".join(parts)


def workdir(run: Dict[str, Any], u: Dict[str, Any]) -> str:
    return posixpath.normpath(posixpath.join(u["path"], run["cwd_rel"]))


def diff_cmd(run: Dict[str, Any], u: Dict[str, Any]) -> str:
    p, base = q(u["path"]), q(run["base"])
    return (f"git -C {p} add -A >/dev/null 2>&1; "
            f"echo \"stat=$(git -C {p} diff --cached --shortstat {base})\"; "
            f"git -C {p} diff --cached --name-only {base} | head -50 | sed 's/^/file=/'")


def patch_path(run: Dict[str, Any], label: str) -> str:
    return posixpath.join(run["base_dir"], f"{label}.patch")


def adopt_cmd(run: Dict[str, Any], label: str) -> str:
    u = run["universes"][label]
    p, base, patch = q(u["path"]), q(run["base"]), q(patch_path(run, label))
    return (f"git -C {p} add -A && git -C {p} diff --cached --binary {base} > {patch} && "
            f"git -C {q(run['root'])} apply --whitespace=nowarn {patch} && "
            f"git -C {q(run['root'])} status --short | head -40")


def close_cmd(run: Dict[str, Any]) -> str:
    parts = [f"git -C {q(run['root'])} worktree remove --force {q(u['path'])} 2>/dev/null"
             for u in run["universes"].values()]
    parts.append(f"git -C {q(run['root'])} worktree prune")
    parts.append(f"rm -f {q(run['base_dir'])}/*.patch")
    parts.append(f"rmdir {q(run['base_dir'])} 2>/dev/null")
    return "; ".join(parts) + "; true"


def parse_diff(output: str) -> Dict[str, Any]:
    stat = ""
    files: List[str] = []
    for line in (output or "").splitlines():
        if line.startswith("stat="):
            stat = line[5:]
        elif line.startswith("file="):
            files.append(line[5:])
    nums = {k: 0 for k in ("files", "insertions", "deletions")}
    for count, word in re.findall(r"(\d+) (file|insertion|deletion)", stat):
        nums[{"file": "files", "insertion": "insertions", "deletion": "deletions"}[word]] = int(count)
    nums["lines"] = nums["insertions"] + nums["deletions"]
    nums["names"] = files
    return nums


# ---------------------------------------------------------------------------
# pinning a subagent to its worktree
# ---------------------------------------------------------------------------

def _inside(path: str, folder: str) -> bool:
    path, folder = posixpath.normpath(path), posixpath.normpath(folder)
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def remap(path: str, run: Dict[str, Any], u: Dict[str, Any]) -> Tuple[str, str]:
    """Map a path seen by a subagent into its universe.

    Returns (new_path, kind) where kind is "same", "mapped", "foreign" (another
    universe) or "outside" (not in the project at all).
    """
    if not isinstance(path, str) or not path:
        return path, "same"
    expanded = os.path.expanduser(path) if path.startswith("~") else path
    root = run["root"]
    if not expanded.startswith("/"):
        return posixpath.normpath(posixpath.join(workdir(run, u), expanded)), "mapped"
    if _inside(expanded, u["path"]):
        return expanded, "same"
    for other in run["universes"].values():
        if other is not u and _inside(expanded, other["path"]):
            return expanded, "foreign"
    if _inside(expanded, root):
        return posixpath.normpath(posixpath.join(u["path"], posixpath.relpath(expanded, root))), "mapped"
    return expanded, "outside"


def rewrite(tool_name: str, args: Dict[str, Any], run: Dict[str, Any], u: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The pre_tool_call directive that keeps a subagent inside its universe."""
    args = args if isinstance(args, dict) else {}
    if tool_name == "terminal":
        changes: Dict[str, Any] = {}
        wd = args.get("workdir")
        new_wd, kind = remap(wd, run, u) if wd else (workdir(run, u), "mapped")
        if kind == "foreign":
            return {"action": "block", "message": f"[Multiverse] {wd} belongs to another universe. Work only in {u['path']}."}
        if kind == "outside" and wd:
            new_wd = workdir(run, u)
        changes["workdir"] = new_wd
        command = str(args.get("command") or "")
        for other in run["universes"].values():
            if other is not u and other["path"] in command:
                return {"action": "block", "message": f"[Multiverse] This command touches another universe ({other['label']}). Work only in {u['path']}."}
        if run["root"] in command:
            changes["command"] = re.sub(re.escape(run["root"]) + r"(?=/|\b|$)", u["path"], command)
        return {"action": "modify", "args": changes}
    if tool_name in FILE_TOOLS:
        changes = {}
        for key in ("path", "file_path"):
            if isinstance(args.get(key), str):
                new, kind = remap(args[key], run, u)
                if kind == "foreign":
                    return {"action": "block", "message": f"[Multiverse] {args[key]} belongs to another universe. Work only in {u['path']}."}
                if kind == "outside" and tool_name in WRITE_TOOLS:
                    continue  # scratch files outside the project are fine
                if new != args[key]:
                    changes[key] = new
        body = args.get("patch")
        if tool_name == "patch" and isinstance(body, str):
            def fix(m):
                new, kind = remap(m.group(2), run, u)
                return m.group(1) + (new if kind in ("same", "mapped") else m.group(2))
            new_body = re.sub(r"^(\*\*\*\s+(?:Update|Add|Delete|Move to)\s+File:\s*)(.+?)\s*$", fix, body, flags=re.MULTILINE)
            if new_body != body:
                changes["patch"] = new_body
        return {"action": "modify", "args": changes} if changes else None
    if tool_name == "execute_code":
        code = str(args.get("code") or "")
        if run["root"] in code:
            return {"action": "modify", "args": {"code": re.sub(re.escape(run["root"]) + r"(?=/|\b|$)", u["path"], code)}}
        return None
    if tool_name == "delegate_task":
        return {"action": "block", "message": "[Multiverse] A universe cannot start more subagents. Do the work yourself."}
    return None


# ---------------------------------------------------------------------------
# tasks, judging and the scoreboard
# ---------------------------------------------------------------------------

def delegate_tasks(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    checks = "\n".join(f"  - {c}" for c in run["checks"])
    tasks = []
    for u in run["universes"].values():
        tasks.append({
            "goal": f"{marker(run['id'], u['label'])} {run['goal']}\n\nApproach for this attempt: {u['approach']}",
            "context": (
                f"You are one of {len(run['universes'])} parallel attempts at the same task. You work in your own git "
                f"worktree at {u['path']} (a copy of {run['root']} at {run['base_kind']}). Your file and terminal tools "
                f"are pinned to it, so use project paths as usual. When you are done, run these checks:\n{checks}\n"
                "Do not commit and do not push. End with three lines: what you changed, the check results, the risks."
            ),
        })
    return tasks


def score(u: Dict[str, Any]) -> Tuple:
    passed = sum(1 for r in u.get("results", []) if r.get("passed"))
    total = len(u.get("results", []))
    lines = (u.get("diff") or {}).get("lines", 0)
    empty = 1 if lines == 0 else 0  # an untouched tree should not win by being small
    failed_child = 1 if u.get("status") == "failed" else 0
    return (-passed, failed_child, empty, lines, (u.get("diff") or {}).get("files", 0), u["label"])


def rank(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    ordered = sorted(run["universes"].values(), key=score)
    total = len(run["checks"])
    winner = None
    for u in ordered:
        passed = sum(1 for r in u.get("results", []) if r.get("passed"))
        u["score"] = {"passed": passed, "total": total, "all_pass": passed == total and total > 0}
        if winner is None and u["score"]["all_pass"] and (u.get("diff") or {}).get("lines", 0) > 0:
            winner = u["label"]
    run["winner"] = winner
    run["status"] = "judged"
    return ordered


def public_run(run: Dict[str, Any]) -> Dict[str, Any]:
    """The run as the Desktop half shows it: no summaries, no outputs, short strings."""
    out = {k: run.get(k) for k in ("id", "status", "winner", "base_kind", "session", "created_at")}
    out["goal"] = (run.get("goal") or "")[:200]
    out["base"] = (run.get("base") or "")[:12]
    out["checks"] = list(run.get("checks") or [])[:12]
    out["adopted"] = run.get("adopted")
    universes = []
    for u in sorted(run["universes"].values(), key=score):
        s = u.get("score") or {}
        d = u.get("diff") or {}
        universes.append({
            "label": u["label"], "model": u.get("model") or "", "approach": u["approach"][:120],
            "status": u.get("status"), "calls": int(u.get("calls", 0)),
            "passed": s.get("passed"), "total": s.get("total"), "all_pass": s.get("all_pass"),
            "files": d.get("files"), "insertions": d.get("insertions"), "deletions": d.get("deletions"),
            "duration_ms": u.get("duration_ms"),
        })
    out["universes"] = universes
    return out


def scoreboard(run: Dict[str, Any]) -> str:
    ordered = sorted(run["universes"].values(), key=score)
    lines = [f"**Multiverse {run['id']}** · {len(ordered)} universes · {len(run['checks'])} checks · base {run['base_kind']} `{run['base'][:10]}`", "",
             "| | Universe | Model | Approach | Checks | Diff |", "|---|---|---|---|---|---|"]
    for i, u in enumerate(ordered):
        s = u.get("score") or {}
        d = u.get("diff") or {}
        medal = "🏆" if u["label"] == run.get("winner") else ("❌" if u.get("status") == "failed" else f"{i + 1}.")
        checks = f"{s.get('passed', 0)}/{s.get('total', len(run['checks']))}" if s else "not judged"
        diff = f"+{d.get('insertions', 0)} −{d.get('deletions', 0)} in {d.get('files', 0)} files" if d else "?"
        approach = u["approach"] if len(u["approach"]) <= 48 else u["approach"][:47] + "…"
        lines.append(f"| {medal} | {u['label']} | {u.get('model') or 'default'} | {approach} | {checks} | {diff} |")
    if run.get("winner"):
        lines += ["", f"Winner: **{run['winner']}**. Ask the agent to adopt it (multiverse_adopt)."]
    elif run["status"] == "judged":
        lines += ["", "No universe passed every check with a real change. Read the summaries, then adopt one by label or close the run."]
    return "\n".join(lines)
