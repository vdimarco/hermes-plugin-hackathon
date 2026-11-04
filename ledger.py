"""Evidence ledger: a record of what the agent did, built from post_tool_call.

Each tool call becomes one Event. Vouch keeps the events per session, in memory,
in a bounded deque. It stores the command text, the paths, the exit code and a
short tail of the output. It does not store file contents.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Deque, Dict, List, Optional

MAX_EVENTS_PER_SESSION = 400
MAX_SESSIONS = 64
OUTPUT_TAIL_CHARS = 600

# Tools that change files. patch also accepts a V4A body with many paths.
WRITE_TOOLS = {"write_file", "patch"}
READ_TOOLS = {"read_file", "search_files"}
CODE_TOOLS = {"execute_code"}

# ---------------------------------------------------------------------------
# Command categories. A shell command can match more than one category, for
# example "ruff check . && pytest -q" is both lint and test.
# ---------------------------------------------------------------------------
_W = r"(?:^|[\s;&|()`'\"/])"  # start of a command word

CATEGORY_PATTERNS: Dict[str, re.Pattern] = {
    "test": re.compile(
        _W + r"(?:"
        r"pytest|py\.test|python[\d.]*\s+-m\s+(?:pytest|unittest)|tox|nox"
        r"|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?::[\w:-]+)?"
        r"|npx\s+(?:jest|vitest|mocha|playwright\s+test)|jest|vitest|mocha|ava"
        r"|go\s+test|cargo\s+(?:test|nextest)|deno\s+test|swift\s+test|dotnet\s+test"
        r"|mvn\s+(?:\S+\s+)*(?:test|verify)|(?:\./)?gradlew?\s+(?:\S+\s+)*test"
        r"|make\s+(?:\S+\s+)*(?:test|check)|ctest|rspec|phpunit|mix\s+test|bundle\s+exec\s+rspec"
        r"|playwright\s+test|cypress\s+run|bats"
        r")\b",
        re.IGNORECASE,
    ),
    "build": re.compile(
        _W + r"(?:"
        r"(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?build(?::[\w:-]+)?"
        r"|cargo\s+build|go\s+build|tsc(?!\s+--noEmit)|vite\s+build|next\s+build|webpack"
        r"|mvn\s+(?:\S+\s+)*(?:package|compile|install)|(?:\./)?gradlew?\s+(?:\S+\s+)*(?:build|assemble)"
        r"|dotnet\s+build|swift\s+build|python[\d.]*\s+-m\s+build|docker\s+build|make(?:\s+(?:all|build))?\s*(?:$|[;&|])"
        r"|cmake\s+--build|bazel\s+build|xcodebuild"
        r")",
        re.IGNORECASE,
    ),
    "lint": re.compile(
        _W + r"(?:"
        r"ruff|flake8|pylint|mypy|pyright|black\s+--check|isort\s+--check"
        r"|eslint|prettier\s+--check|tsc\s+--noEmit|biome\s+(?:check|lint)"
        r"|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:lint|typecheck|type-check|check)"
        r"|cargo\s+(?:clippy|check|fmt\s+--check)|golangci-lint|go\s+vet|shellcheck|rubocop|stylelint"
        r")\b",
        re.IGNORECASE,
    ),
    "commit": re.compile(_W + r"git\s+(?:-C\s+\S+\s+)?commit\b", re.IGNORECASE),
    "push": re.compile(_W + r"git\s+(?:-C\s+\S+\s+)?push\b", re.IGNORECASE),
    "install": re.compile(
        _W + r"(?:"
        r"pip[\d.]*\s+install|python[\d.]*\s+-m\s+pip\s+install|uv\s+(?:pip\s+install|add|sync)"
        r"|poetry\s+(?:add|install)|pipx\s+install|conda\s+install"
        r"|(?:npm|pnpm|bun)\s+(?:install|i|add|ci)|yarn\s+(?:install|add)"
        r"|cargo\s+(?:add|install)|go\s+(?:get|install)|gem\s+install|bundle\s+install"
        r"|apt(?:-get)?\s+install|brew\s+install|apk\s+add|dnf\s+install|composer\s+(?:require|install)"
        r")\b",
        re.IGNORECASE,
    ),
    "deploy": re.compile(
        _W + r"(?:"
        r"vercel(?:\s+deploy|\s+--prod)?|netlify\s+deploy|fly(?:ctl)?\s+deploy|wrangler\s+(?:deploy|publish)"
        r"|firebase\s+deploy|gcloud\s+(?:\S+\s+)*deploy|kubectl\s+(?:apply|rollout\s+restart|set\s+image)|helm\s+(?:install|upgrade)"
        r"|terraform\s+apply|pulumi\s+up|serverless\s+deploy|sls\s+deploy|railway\s+up"
        r"|git\s+push\s+heroku|docker\s+push|eb\s+deploy|amplify\s+publish|cdk\s+deploy|sam\s+deploy"
        r")\b",
        re.IGNORECASE,
    ),
}

# Output that shows a failure even when the exit code is 0, for example
# "pytest | tail -5" or "npm test || true".
_FAIL_MARKERS = re.compile(
    r"(?:"
    r"\b[1-9]\d*\s+(?:failed|failing|errors?)\b"       # pytest "2 failed", mocha "3 failing"
    r"|^FAILED\b|^FAIL[\s:]|---\s*FAIL:"                # pytest, go, jest
    r"|test result:\s*FAILED"                           # cargo
    r"|Tests:\s+.*\b[1-9]\d*\s+failed"                  # jest / vitest summary
    r"|^ERROR[:\s]|\bTraceback \(most recent call last\)"
    r"|npm ERR!|error\[E\d+\]|BUILD FAILED|^=+ FAILURES =+$"
    r"|^\s*Found [1-9]\d* errors?\b|\b[1-9]\d* problems? \("  # mypy/tsc, eslint
    r")",
    re.IGNORECASE | re.MULTILINE,
)
_NO_TESTS = re.compile(
    r"(?:collected 0 items|no tests ran|no tests found|0 tests?\b|ran 0 tests|No test files found|no test files)",
    re.IGNORECASE,
)
_PASS_MARKERS = re.compile(
    r"(?:\b[1-9]\d*\s+passed\b|\bOK\b(?:\s*\(|$)|test result:\s*ok|Tests:\s+.*\bpassed|\bok\s+\S+\s+[\d.]+s\b|All checks passed|\bPASS\b)",
    re.IGNORECASE | re.MULTILINE,
)
_OUTPUT_JUDGED = {"test", "build", "lint", "install", "deploy"}
_V4A_PATH = re.compile(r"^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*(.+?)\s*$", re.MULTILINE)
_SHELL_WRITE = re.compile(
    r"(?:>>?|\btee\b|\bsed\s+-i|\bperl\s+-[pi]+|\bmv\b|\bcp\b|\btouch\b|\brm\b|\bmkdir\b|\bgit\s+(?:mv|rm|checkout|restore|apply)|\bpatch\b)"
)


@dataclass
class Event:
    seq: int
    ts: float
    tool: str
    ok: bool
    categories: List[str] = field(default_factory=list)
    command: str = ""
    paths: List[str] = field(default_factory=list)
    exit_code: Optional[int] = None
    background: bool = False
    output_tail: str = ""
    failure_in_output: bool = False
    pass_in_output: bool = False
    no_tests: bool = False
    shell_write: bool = False
    turn_id: str = ""

    @property
    def is_write(self) -> bool:
        return self.tool in WRITE_TOOLS or (self.tool == "terminal" and self.shell_write)

    @property
    def is_run(self) -> bool:
        """A call that executed something the agent can cite as a check."""
        return self.tool in CODE_TOOLS or (self.tool == "terminal" and bool(self.command)) or self.tool.startswith("browser")

    @property
    def succeeded(self) -> bool:
        """True only for a finished call with a clean exit and clean output."""
        if not self.ok or self.background:
            return False
        if self.exit_code not in (None, 0):
            return False
        return not self.failure_in_output

    def describe(self) -> str:
        if self.tool == "terminal":
            cmd = _short(self.command, 80)
            if self.background:
                return f"`{cmd}` (started in background, no exit code)"
            code = "?" if self.exit_code is None else str(self.exit_code)
            note = ""
            if self.exit_code == 0 and self.failure_in_output:
                note = ", but the output shows failures"
            elif self.no_tests:
                note = ", but no tests ran"
            return f"`{cmd}` exited {code}{note}"
        if self.paths:
            state = "ok" if self.ok else "failed"
            return f"{self.tool} {', '.join(self.paths[:3])} ({state})"
        return f"{self.tool} ({'ok' if self.ok else 'failed'})"

    def to_public(self) -> Dict[str, Any]:
        data = asdict(self)
        data["succeeded"] = self.succeeded
        return data


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            return {"output": result}
        return parsed if isinstance(parsed, dict) else {"output": result}
    return {}


def classify_command(command: str) -> List[str]:
    return [name for name, pattern in CATEGORY_PATTERNS.items() if pattern.search(command or "")]


def _paths_from_args(tool: str, args: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    for key in ("path", "file_path", "filename"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    body = args.get("patch")
    if tool == "patch" and isinstance(body, str):
        paths.extend(m.strip() for m in _V4A_PATH.findall(body))
    seen: List[str] = []
    for p in paths:
        if p not in seen:
            seen.append(p)
    return seen


def build_event(seq: int, tool_name: str, args: Any, result: Any, status: Optional[str], turn_id: str = "") -> Event:
    args = args if isinstance(args, dict) else {}
    data = _parse_result(result)
    ok = (status or "ok") == "ok" and not data.get("error") and data.get("success") is not False
    event = Event(seq=seq, ts=time.time(), tool=str(tool_name or ""), ok=ok, turn_id=str(turn_id or ""))
    event.paths = _paths_from_args(event.tool, args)

    if event.tool == "terminal":
        event.command = str(args.get("command") or "")
        event.background = bool(args.get("background"))
        code = data.get("exit_code")
        event.exit_code = code if isinstance(code, int) else None
        output = str(data.get("output") or "")
        if data.get("error"):
            output = f"{output}\n{data.get('error')}"
        event.output_tail = output[-OUTPUT_TAIL_CHARS:]
        event.categories = classify_command(event.command)
        # Only checks are judged on their output. A commit message that says
        # "fix 2 failed tests" is not a failure.
        if set(event.categories) & _OUTPUT_JUDGED:
            event.failure_in_output = bool(_FAIL_MARKERS.search(output))
        event.pass_in_output = bool(_PASS_MARKERS.search(output))
        event.no_tests = "test" in event.categories and bool(_NO_TESTS.search(output)) and not event.pass_in_output
        event.shell_write = bool(_SHELL_WRITE.search(event.command))
        if event.exit_code not in (None, 0):
            event.ok = False
    elif event.tool in CODE_TOOLS:
        code = str(args.get("code") or "")
        event.command = _short(code, 200)
        output = str(data.get("output") or data.get("stdout") or "")
        event.output_tail = output[-OUTPUT_TAIL_CHARS:]
        event.failure_in_output = bool(_FAIL_MARKERS.search(output))
    return event


class Ledger:
    """Per-session event store. Safe to call from many threads."""

    def __init__(self, max_events: int = MAX_EVENTS_PER_SESSION, max_sessions: int = MAX_SESSIONS) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, Deque[Event]] = {}
        self._order: Deque[str] = deque()
        self._seq = 0
        self._max_events = max_events
        self._max_sessions = max_sessions

    @staticmethod
    def key(session_id: Optional[str], task_id: Optional[str] = None) -> str:
        return str(session_id or task_id or "_default")

    def record(self, session_key: str, tool_name: str, args: Any, result: Any,
               status: Optional[str] = None, turn_id: str = "") -> Event:
        with self._lock:
            self._seq += 1
            event = build_event(self._seq, tool_name, args, result, status, turn_id)
            events = self._sessions.get(session_key)
            if events is None:
                events = deque(maxlen=self._max_events)
                self._sessions[session_key] = events
                self._order.append(session_key)
                while len(self._order) > self._max_sessions:
                    old = self._order.popleft()
                    self._sessions.pop(old, None)
            events.append(event)
            return event

    def events(self, session_key: str) -> List[Event]:
        with self._lock:
            return list(self._sessions.get(session_key, ()))

    def latest_key(self) -> Optional[str]:
        """The session that recorded the newest event, if any."""
        with self._lock:
            best, best_seq = None, -1
            for key, events in self._sessions.items():
                if events and events[-1].seq > best_seq:
                    best, best_seq = key, events[-1].seq
            return best

    def forget(self, session_key: str) -> None:
        with self._lock:
            self._sessions.pop(session_key, None)
            try:
                self._order.remove(session_key)
            except ValueError:
                pass
