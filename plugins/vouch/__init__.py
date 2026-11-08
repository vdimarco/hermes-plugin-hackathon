"""Vouch: the agent's "done" must match the tool record.

Vouch watches every tool call (post_tool_call) and keeps a per-session record of
what really happened: commands and their exit codes, files written, test, build,
lint, git, install and deploy runs. When the agent is about to finish, Vouch reads
its reply, finds claims such as "all tests pass" or "I updated src/app.py", and
checks each one against the record.

* pre_verify: after code edits, a false or unsupported claim sends the agent back
  to work once, with the exact contradiction (bounded by ``max_nudges``).
* transform_llm_output: claims that are still unsupported get a short footer in
  the reply the user sees.
* Tools ``vouch_evidence`` and ``vouch_check`` let the model inspect the record.
* ``/vouch`` shows the last verdicts; ``hermes vouch stats`` shows totals.

No network calls, no shell commands, no background threads, no credentials.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional

from . import checker, desktop_events, schemas
from .claims import KINDS
from .ledger import Ledger

logger = logging.getLogger(__name__)

TOOLSET = "vouch"
OWN_TOOLS = {"vouch_evidence", "vouch_check"}
MODES = ("nudge", "annotate", "off")
RECENT_LIMIT = 50

_LEDGER = Ledger()
_LAST_REPORTS: "OrderedDict[str, checker.Report]" = OrderedDict()
_LOCK = threading.Lock()


class Vouch:
    """Holds the plugin context. One instance per register() call."""

    def __init__(self, ctx: Any, ledger: Optional[Ledger] = None) -> None:
        self.ctx = ctx
        self.ledger = ledger or _LEDGER
        self.reports: "OrderedDict[str, checker.Report]" = _LAST_REPORTS if ledger is None else OrderedDict()

    # -- config -----------------------------------------------------------------
    def _cfg(self, key: str, default: Any) -> Any:
        try:
            value = self.ctx.get_config(key, default)
        except Exception:
            return default
        return default if value is None else value

    def mode(self) -> str:
        value = str(self._cfg("mode", "nudge")).strip().lower()
        return value if value in MODES else "nudge"

    def max_nudges(self) -> int:
        try:
            return max(0, int(self._cfg("max_nudges", 1)))
        except (TypeError, ValueError):
            return 1

    def footer_enabled(self) -> bool:
        return _truthy(self._cfg("footer", True))

    def footer_verified(self) -> bool:
        return _truthy(self._cfg("footer_verified", False))

    def kinds(self):
        value = self._cfg("kinds", [])
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",")]
        chosen = [k for k in (value or []) if k in KINDS]
        return chosen or list(KINDS)

    # -- reports and stats -----------------------------------------------------------
    def _remember(self, key: str, report: checker.Report) -> None:
        with _LOCK:
            self.reports[key] = report
            self.reports.move_to_end(key)
            while len(self.reports) > 64:
                self.reports.popitem(last=False)

    def _record_stats(self, key: str, report: checker.Report, nudged: bool = False) -> None:
        if not report.findings and not nudged:
            return
        try:
            state = self.ctx.state
            stats = state.get("stats", None) or {}
            if not nudged:
                for f in report.findings:
                    stats[f.status] = int(stats.get(f.status, 0)) + 1
                    by_kind = stats.setdefault("by_kind", {})
                    entry = by_kind.setdefault(f.kind, {})
                    entry[f.status] = int(entry.get(f.status, 0)) + 1
                stats["replies"] = int(stats.get("replies", 0)) + 1
            else:
                stats["nudges"] = int(stats.get("nudges", 0)) + 1
            stats["since"] = stats.get("since") or time.strftime("%Y-%m-%d")
            state.set("stats", stats)
            if report.problems:
                recent = list(state.get("recent", None) or [])
                for f in report.problems:
                    recent.append({
                        "ts": int(time.time()), "session": key[:40], "stage": "draft" if nudged else "final",
                        "kind": f.kind, "status": f.status, "claim": f.claim[:160], "reason": f.reason[:200],
                    })
                state.set("recent", recent[-RECENT_LIMIT:])
        except Exception:
            logger.debug("vouch: could not update stats", exc_info=True)

    def _session_key(self, session_id: Optional[str], task_id: Optional[str] = None) -> str:
        return Ledger.key(session_id, task_id)

    def _resolve_key(self, session_id: Optional[str], task_id: Optional[str] = None) -> str:
        key = self._session_key(session_id, task_id)
        if self.ledger.events(key) or key in self.reports:
            return key
        latest = self.ledger.latest_key()
        return latest or key

    # -- hooks -------------------------------------------------------------------
    def on_post_tool_call(self, tool_name: str = "", args: Any = None, result: Any = None,
                          session_id: Optional[str] = None, task_id: Optional[str] = None,
                          turn_id: Optional[str] = None, status: Optional[str] = None, **kwargs: Any) -> None:
        if not tool_name or tool_name in OWN_TOOLS:
            return
        try:
            self.ledger.record(self._session_key(session_id, task_id), tool_name, args, result,
                               status=status, turn_id=turn_id or "")
        except Exception:
            logger.debug("vouch: could not record %s", tool_name, exc_info=True)

    def on_pre_verify(self, session_id: str = "", attempt: int = 0, final_response: str = "",
                      **kwargs: Any) -> Optional[Dict[str, str]]:
        if self.mode() != "nudge" or attempt >= self.max_nudges():
            return None
        try:
            key = self._session_key(session_id)
            report = checker.check_reply(final_response or "", self.ledger.events(key), self.kinds())
            if not report.problems:
                return None
            self._record_stats(key, report, nudged=True)
            desktop_events.emit("report", _desktop_report(key, report, stage="draft"))
            return {"action": "continue", "message": checker.format_nudge(report)}
        except Exception:
            logger.debug("vouch: pre_verify failed", exc_info=True)
            return None

    def on_transform_llm_output(self, response_text: str = "", session_id: str = "",
                                **kwargs: Any) -> Optional[str]:
        if not isinstance(response_text, str) or not response_text.strip():
            return None
        footer = ""
        try:
            key = self._session_key(session_id)
            if self.mode() != "off":
                report = checker.check_reply(response_text, self.ledger.events(key), self.kinds())
                self._remember(key, report)
                self._record_stats(key, report)
                if report.findings:
                    desktop_events.emit("report", _desktop_report(key, report, stage="final"))
                if self.footer_enabled():
                    footer = checker.format_footer(report, show_verified=self.footer_verified())
        except Exception:
            logger.debug("vouch: transform failed", exc_info=True)
        # Hermes keeps only the first reply rewrite, so Vouch also carries Contract's status line
        # (Contract yields its own footer while Vouch is enabled).
        contract_line = self._contract_footer_line(session_id)
        if contract_line:
            footer = (footer + "\n\n" + contract_line) if footer else ("\n---\n" + contract_line)
        return response_text.rstrip() + "\n" + footer if footer else None

    def _contract_footer_line(self, session_id: str) -> str:
        try:
            if not self.ctx.has_plugin("contract"):
                return ""
            raw = self.ctx.dispatch_tool("contract_status", {}, session_id=session_id, footer_request=True)
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            line = data.get("footer_line") if isinstance(data, dict) else ""
            return line if isinstance(line, str) else ""
        except Exception:
            logger.debug("vouch: contract footer line unavailable", exc_info=True)
            return ""

    def on_session_delete(self, session_id: str = "", **kwargs: Any) -> None:
        key = self._session_key(session_id)
        self.ledger.forget(key)
        with _LOCK:
            self.reports.pop(key, None)

    # -- tools -------------------------------------------------------------------
    def tool_evidence(self, args: Dict[str, Any], session_id: Optional[str] = None,
                      task_id: Optional[str] = None, **kwargs: Any) -> str:
        try:
            args = args or {}
            kind = str(args.get("kind") or "all")
            try:
                limit = min(100, max(1, int(args.get("limit") or 20)))
            except (TypeError, ValueError):
                limit = 20
            events = self.ledger.events(self._resolve_key(session_id, task_id))
            picked = [e for e in events if _event_matches(e, kind)][-limit:]
            return json.dumps({
                "kind": kind,
                "total_events": len(events),
                "events": [_public_event(e) for e in picked],
            })
        except Exception as exc:
            return json.dumps({"error": f"vouch_evidence failed: {exc}"})

    def tool_check(self, args: Dict[str, Any], session_id: Optional[str] = None,
                   task_id: Optional[str] = None, **kwargs: Any) -> str:
        try:
            text = str((args or {}).get("text") or "")
            if not text.strip():
                return json.dumps({"error": "text is required"})
            events = self.ledger.events(self._resolve_key(session_id, task_id))
            report = checker.check_reply(text, events, self.kinds())
            data = report.to_dict()
            data["advice"] = (
                "All claims are supported." if not report.problems else
                "Fix or remove the claims that are not verified before you send the reply."
            )
            return json.dumps(data)
        except Exception as exc:
            return json.dumps({"error": f"vouch_check failed: {exc}"})

    # -- slash command ------------------------------------------------------------------
    def command(self, raw_args: str = "", session_id: Optional[str] = None, **kwargs: Any) -> str:
        sub = (raw_args or "").strip().lower()
        key = self._resolve_key(session_id)
        if sub in ("", "status", "last"):
            head = f"Vouch mode: {self.mode()} · events on record: {len(self.ledger.events(key))}"
            return head + "\n" + checker.format_status(self.reports.get(key))
        if sub in ("evidence", "log", "record"):
            events = self.ledger.events(key)[-25:]
            if not events:
                return "No tool calls on record for this session yet."
            return "\n".join(f"#{e.seq:<4} {'✓' if e.succeeded else '✗'} {e.describe()}" for e in events)
        if sub == "stats":
            return format_stats(self.ctx)
        return ("Usage: /vouch            last verdicts for this session\n"
                "       /vouch evidence   the tool record Vouch checks against\n"
                "       /vouch stats      totals across sessions")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _desktop_report(key: str, report: checker.Report, stage: str) -> Dict[str, Any]:
    """The payload the Desktop half shows: counts plus the claims that need attention."""
    return {
        "session": key,
        "stage": stage,          # "draft": the agent was sent back; "final": the reply the user got
        "ts": int(time.time()),
        "counts": report.counts(),
        "problems": [{"kind": f.kind, "status": f.status, "label": checker._label(f).strip("“”"),
                      "reason": f.reason[:200], "claim": f.claim[:120]} for f in report.problems[:6]],
    }


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(value)


_KIND_FILTERS = {
    "tests": lambda e: "test" in e.categories,
    "build": lambda e: "build" in e.categories,
    "lint": lambda e: "lint" in e.categories,
    "edits": lambda e: e.is_write,
    "git": lambda e: bool({"commit", "push"} & set(e.categories)),
    "install": lambda e: "install" in e.categories,
    "deploy": lambda e: "deploy" in e.categories,
    "failures": lambda e: not e.succeeded and not e.background and e.tool not in ("read_file", "search_files"),
}


def _event_matches(event, kind: str) -> bool:
    check = _KIND_FILTERS.get(kind)
    return True if check is None else check(event)


def _public_event(event) -> Dict[str, Any]:
    data = {
        "seq": event.seq, "tool": event.tool, "summary": event.describe(),
        "succeeded": event.succeeded, "categories": event.categories,
    }
    if event.paths:
        data["paths"] = event.paths
    if event.tool == "terminal":
        data["exit_code"] = event.exit_code
        if not event.succeeded and event.output_tail:
            data["output_tail"] = event.output_tail[-300:]
    return data


def format_stats(ctx: Any) -> str:
    try:
        stats = ctx.state.get("stats", None) or {}
        recent = ctx.state.get("recent", None) or []
    except Exception:
        stats, recent = {}, []
    if not stats:
        return "Vouch has not checked any replies yet."
    total = sum(int(stats.get(s, 0)) for s in ("verified", "unverified", "stale", "contradicted"))
    lines = [
        f"Vouch since {stats.get('since', '?')}: {stats.get('replies', 0)} replies, {total} claims checked",
        f"  ✅ verified {stats.get('verified', 0)}   ⚠️ unverified {stats.get('unverified', 0)}   "
        f"⏳ stale {stats.get('stale', 0)}   ❌ contradicted {stats.get('contradicted', 0)}",
        f"  agent sent back to fix a claim: {stats.get('nudges', 0)} times",
    ]
    by_kind = stats.get("by_kind") or {}
    if by_kind:
        lines.append("  by claim type:")
        for kind, entry in sorted(by_kind.items(), key=lambda kv: -sum(kv[1].values())):
            bad = sum(v for s, v in entry.items() if s != "verified")
            lines.append(f"    {kind:<9} {sum(entry.values()):>4} checked, {bad} flagged")
    if recent:
        lines.append("  latest flags:")
        for item in recent[-5:]:
            when = time.strftime("%m-%d %H:%M", time.localtime(item.get("ts", 0)))
            stage = "draft, agent sent back" if item.get("stage") == "draft" else "final reply"
            lines.append(f"    {when} [{item.get('status')}, {stage}] {item.get('kind')}: {item.get('reason', '')[:90]}")
    return "\n".join(lines)


def _cli_setup(parser) -> None:
    parser.add_argument("action", nargs="?", default="stats", choices=["stats", "log", "reset"],
                        help="stats: totals (default); log: recent flags as JSON; reset: clear totals")


def _make_cli_handler(ctx: Any):
    def handler(args) -> int:
        action = getattr(args, "action", "stats")
        if action == "log":
            try:
                print(json.dumps(ctx.state.get("recent", None) or [], indent=2, ensure_ascii=False))
            except Exception as exc:
                print(f"vouch: cannot read state: {exc}")
                return 1
            return 0
        if action == "reset":
            try:
                ctx.state.set("stats", {})
                ctx.state.set("recent", [])
            except Exception as exc:
                print(f"vouch: cannot reset state: {exc}")
                return 1
            print("Vouch totals cleared.")
            return 0
        print(format_stats(ctx))
        return 0
    return handler


def register(ctx: Any) -> None:
    """Hermes entry point."""
    vouch = Vouch(ctx)
    ctx.register_hook("post_tool_call", vouch.on_post_tool_call)
    ctx.register_hook("pre_verify", vouch.on_pre_verify)
    ctx.register_hook("transform_llm_output", vouch.on_transform_llm_output)
    ctx.register_hook("on_session_delete", vouch.on_session_delete)
    ctx.register_tool(name="vouch_evidence", toolset=TOOLSET, schema=schemas.VOUCH_EVIDENCE,
                      handler=vouch.tool_evidence, emoji="🧾")
    ctx.register_tool(name="vouch_check", toolset=TOOLSET, schema=schemas.VOUCH_CHECK,
                      handler=vouch.tool_check, emoji="🧾")
    ctx.register_command("vouch", handler=vouch.command,
                         description="Show which claims in the last reply the tool record supports",
                         args_hint="[evidence|stats]")
    ctx.register_cli_command(name="vouch", help="Vouch claim-check totals",
                             setup_fn=_cli_setup, handler_fn=_make_cli_handler(ctx),
                             description="Show how often the agent's claims matched the tool record.")
