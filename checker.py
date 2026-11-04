"""Checker: match each claim to the evidence ledger and give a verdict.

Verdicts:
  verified      the record supports the claim
  unverified    the record has nothing that supports the claim
  stale         the check passed, but files changed after it
  contradicted  the record shows the opposite (a failed run, a failed write)
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional

from .claims import KINDS, Claim, extract_claims
from .ledger import Event, _FAIL_MARKERS

VERIFIED, UNVERIFIED, STALE, CONTRADICTED = "verified", "unverified", "stale", "contradicted"
PROBLEMS = (CONTRADICTED, STALE, UNVERIFIED)
ICONS = {VERIFIED: "✅", UNVERIFIED: "⚠️", STALE: "⏳", CONTRADICTED: "❌"}

_LABEL = {
    "tests": "tests pass", "build": "build passes", "lint": "lint/type checks pass",
    "edit": "file changed", "ran": "command ran", "commit": "changes committed",
    "push": "changes pushed", "install": "package installed", "deploy": "deployed",
    "verified": "tested/verified",
}
_NOUN = {"test": "test", "build": "build", "lint": "lint or type-check", "commit": "git commit",
         "push": "git push", "install": "install", "deploy": "deploy"}


@dataclass
class Finding:
    kind: str
    status: str
    claim: str
    reason: str
    target: str = ""
    hedged: bool = False
    evidence_seq: Optional[int] = None

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class Report:
    findings: List[Finding] = field(default_factory=list)

    @property
    def problems(self) -> List[Finding]:
        return [f for f in self.findings if f.status in PROBLEMS]

    def counts(self) -> Dict[str, int]:
        out = {VERIFIED: 0, UNVERIFIED: 0, STALE: 0, CONTRADICTED: 0}
        for f in self.findings:
            out[f.status] += 1
        return out

    def to_dict(self) -> Dict:
        return {"counts": self.counts(), "findings": [f.to_dict() for f in self.findings]}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _norm_path(path: str) -> List[str]:
    path = path.strip().strip("`'\"").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return [p for p in path.split("/") if p and p != "."]


def paths_match(claimed: str, recorded: str) -> bool:
    """True when one path is a suffix of the other, by whole components."""
    a, b = _norm_path(claimed), _norm_path(recorded)
    if not a or not b:
        return False
    n = min(len(a), len(b))
    return a[-n:] == b[-n:]


def _norm_cmd(cmd: str) -> str:
    return " ".join((cmd or "").split()).lower()


def _first_failure_line(event: Event) -> str:
    match = _FAIL_MARKERS.search(event.output_tail or "")
    if not match:
        return ""
    start = event.output_tail.rfind("\n", 0, match.start()) + 1
    end = event.output_tail.find("\n", match.end())
    line = event.output_tail[start:end if end != -1 else None].strip()
    return line[:120]


def _writes_after(events: Iterable[Event], seq: int) -> List[Event]:
    return [e for e in events if e.seq > seq and e.is_write and e.ok]


def _changed_list(writes: List[Event]) -> str:
    names: List[str] = []
    for w in writes:
        for p in (w.paths or ([w.command[:40]] if w.command else [])):
            if p not in names:
                names.append(p)
    shown = ", ".join(names[:3])
    return shown + (f" and {len(names) - 3} more" if len(names) > 3 else "")


# ---------------------------------------------------------------------------
# per-kind checks
# ---------------------------------------------------------------------------

def _check_check_kind(claim: Claim, events: List[Event], category: str) -> Finding:
    """tests, build, lint: the latest run decides."""
    runs = [e for e in events if category in e.categories]
    noun = _NOUN[category]
    if not runs:
        return Finding(claim.kind, UNVERIFIED, claim.text, f"no {noun} command ran in this session", hedged=claim.hedged)
    last = runs[-1]
    if last.background:
        return Finding(claim.kind, UNVERIFIED, claim.text, f"the last {noun} run, {last.describe()}",
                       hedged=claim.hedged, evidence_seq=last.seq)
    if not last.succeeded:
        detail = _first_failure_line(last)
        reason = f"the last {noun} run, {last.describe()}" + (f": “{detail}”" if detail else "")
        return Finding(claim.kind, CONTRADICTED, claim.text, reason, hedged=claim.hedged, evidence_seq=last.seq)
    if last.no_tests:
        return Finding(claim.kind, UNVERIFIED, claim.text, f"the last {noun} run, {last.describe()}",
                       hedged=claim.hedged, evidence_seq=last.seq)
    later = _writes_after(events, last.seq)
    if later:
        return Finding(claim.kind, STALE, claim.text,
                       f"{last.describe()}, then files changed: {_changed_list(later)}",
                       hedged=claim.hedged, evidence_seq=last.seq)
    return Finding(claim.kind, VERIFIED, claim.text, last.describe(), hedged=claim.hedged, evidence_seq=last.seq)


def _check_action_kind(claim: Claim, events: List[Event], category: str) -> Finding:
    """commit, push, install, deploy: any success counts; a later edit makes a commit stale."""
    runs = [e for e in events if category in e.categories]
    noun = _NOUN[category]
    if claim.kind == "install" and claim.target:
        name = re.split(r"[=<>~^\[@]", claim.target.lstrip("@"), maxsplit=1)[0].lower()
        if name:
            targeted = [e for e in runs if name in e.command.lower()]
            if runs and not targeted:
                return Finding(claim.kind, UNVERIFIED, claim.text,
                               f"install commands ran, but none named {claim.target}",
                               target=claim.target, hedged=claim.hedged)
            runs = targeted
    if not runs:
        return Finding(claim.kind, UNVERIFIED, claim.text, f"no {noun} command ran in this session",
                       target=claim.target, hedged=claim.hedged)
    good = [e for e in runs if e.succeeded]
    if not good:
        last = runs[-1]
        detail = _first_failure_line(last) or (last.output_tail.strip().splitlines() or [""])[-1][:120]
        reason = f"{last.describe()}" + (f": “{detail}”" if detail else "")
        return Finding(claim.kind, CONTRADICTED, claim.text, reason, target=claim.target,
                       hedged=claim.hedged, evidence_seq=last.seq)
    last_good = good[-1]
    if category in ("commit", "push"):
        later = _writes_after(events, last_good.seq)
        if later:
            return Finding(claim.kind, STALE, claim.text,
                           f"{last_good.describe()}, then files changed: {_changed_list(later)}",
                           target=claim.target, hedged=claim.hedged, evidence_seq=last_good.seq)
    return Finding(claim.kind, VERIFIED, claim.text, last_good.describe(), target=claim.target,
                   hedged=claim.hedged, evidence_seq=last_good.seq)


def _check_edit(claim: Claim, events: List[Event]) -> Finding:
    target = claim.target
    tool_writes = [e for e in events if e.tool in ("write_file", "patch") and any(paths_match(target, p) for p in e.paths)]
    leaf = "/".join(_norm_path(target)[-2:]) or target
    shell_writes = [e for e in events if e.tool == "terminal" and e.shell_write and (target in e.command or leaf in e.command)]
    code_writes = [e for e in events if e.tool == "execute_code" and (target in e.command or leaf in e.command)]
    writes = sorted(tool_writes + shell_writes + code_writes, key=lambda e: e.seq)
    if not writes:
        reads = [e for e in events if e.tool in ("read_file", "search_files") and any(paths_match(target, p) for p in e.paths)]
        reason = f"no write to {target} is on record" + (" (it was only read)" if reads else "")
        return Finding("edit", UNVERIFIED, claim.text, reason, target=target, hedged=claim.hedged)
    ok = [e for e in writes if e.ok]
    if not ok:
        return Finding("edit", CONTRADICTED, claim.text, f"every write to {target} failed: {writes[-1].describe()}",
                       target=target, hedged=claim.hedged, evidence_seq=writes[-1].seq)
    return Finding("edit", VERIFIED, claim.text, ok[-1].describe(), target=target, hedged=claim.hedged,
                   evidence_seq=ok[-1].seq)


def _check_ran(claim: Claim, events: List[Event]) -> Finding:
    want = _norm_cmd(claim.target)
    matches = [e for e in events if e.tool == "terminal" and want and (want in _norm_cmd(e.command) or _norm_cmd(e.command) in want and len(_norm_cmd(e.command)) > 3)]
    if not matches:
        return Finding("ran", UNVERIFIED, claim.text, f"no terminal call ran `{claim.target}`",
                       target=claim.target, hedged=claim.hedged)
    last = matches[-1]
    return Finding("ran", VERIFIED, claim.text, last.describe(), target=claim.target, hedged=claim.hedged,
                   evidence_seq=last.seq)


def _check_verified(claim: Claim, events: List[Event]) -> Finding:
    writes = [e for e in events if e.is_write and e.ok]
    # "tested" needs something that ran. "checked" or "confirmed" can rest on any
    # tool call that is not a write: a file read, a web page, an MCP lookup.
    looks = "look" in claim.extras
    runs = [e for e in events if not e.is_write and (e.is_run or looks)]
    last_write = writes[-1].seq if writes else 0
    after = [e for e in runs if e.seq > last_write]
    if after:
        last = after[-1]
        status = VERIFIED if last.succeeded else CONTRADICTED
        return Finding("verified", status, claim.text, last.describe(), hedged=claim.hedged, evidence_seq=last.seq)
    if runs:
        return Finding("verified", STALE, claim.text,
                       f"nothing ran after the last edit ({_changed_list(writes[-1:])})", hedged=claim.hedged)
    what = "nothing was run or read" if looks else "no command or code ran"
    return Finding("verified", UNVERIFIED, claim.text, f"{what} in this session", hedged=claim.hedged)


def check_claim(claim: Claim, events: List[Event]) -> Finding:
    if claim.kind in ("tests", "build", "lint"):
        return _check_check_kind(claim, events, {"tests": "test", "build": "build", "lint": "lint"}[claim.kind])
    if claim.kind in ("commit", "push", "install", "deploy"):
        return _check_action_kind(claim, events, claim.kind)
    if claim.kind == "edit":
        return _check_edit(claim, events)
    if claim.kind == "ran":
        return _check_ran(claim, events)
    return _check_verified(claim, events)


def check_reply(reply: str, events: List[Event], kinds: Optional[Iterable[str]] = None) -> Report:
    allowed = set(kinds) if kinds else set(KINDS)
    claims = [c for c in extract_claims(reply) if c.kind in allowed]
    report = Report([check_claim(c, events) for c in claims])
    order = {CONTRADICTED: 0, STALE: 1, UNVERIFIED: 2, VERIFIED: 3}
    report.findings.sort(key=lambda f: order[f.status])
    return report


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------

def _quote(text: str, limit: int = 90) -> str:
    text = text if len(text) <= limit else text[: limit - 1] + "…"
    return f"“{text}”"


def _label(f: Finding) -> str:
    label = _LABEL.get(f.kind, f.kind)
    if f.target and f.kind in ("edit", "ran", "install"):
        label = f"{label} ({f.target})"
    return f"“{label}”"


def format_nudge(report: Report) -> str:
    """The synthetic user message pre_verify sends back to the agent."""
    lines = ["[Vouch] Your draft reply makes claims that the tool record does not support:"]
    for f in report.problems:
        lines.append(f"- {_label(f)} is {f.status}: {f.reason}. You wrote: {_quote(f.claim, 80)}")
    lines.append(
        "Do one of these before you finish: run the check now and report the real result, "
        "or rewrite the reply so it states only what the record shows. "
        "Do not claim a result that you did not observe. "
        "You can call vouch_evidence to see the record."
    )
    return "\n".join(lines)


def format_footer(report: Report, show_verified: bool = False) -> str:
    """The note appended to the reply the user sees."""
    counts = report.counts()
    total = sum(counts.values())
    problems = report.problems
    if not total or (not problems and not show_verified):
        return ""
    summary = f"{total} claim{'s' if total != 1 else ''} checked"
    parts = [f"{counts[s]} {s}" for s in (CONTRADICTED, STALE, UNVERIFIED, VERIFIED) if counts[s]]
    lines = ["", "---", f"**Vouch** · {summary} · " + " · ".join(parts)]
    shown = report.findings if show_verified else problems
    for f in shown[:6]:
        lines.append(f"- {ICONS[f.status]} **{_label(f)}**: {f.reason}")
    if len(shown) > 6:
        lines.append(f"- …and {len(shown) - 6} more (run /vouch)")
    return "\n".join(lines)


def format_status(report: Optional[Report]) -> str:
    if report is None or not report.findings:
        return "No claims checked yet in this session."
    out = []
    for f in report.findings:
        out.append(f"{ICONS[f.status]} {_label(f)}: {f.status}\n    {f.reason}\n    claim: {_quote(f.claim, 70)}")
    return "\n".join(out)
