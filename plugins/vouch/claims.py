"""Claim extraction: find statements of finished work in an agent reply.

The extractor is deterministic. It reads the reply sentence by sentence and
keeps only first-hand, past or present claims. It drops questions, plans,
instructions to the user, negated clauses and text in code blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

# Claim kinds, in the order the report lists them.
KINDS = ("tests", "build", "lint", "edit", "ran", "commit", "push", "install", "deploy", "verified")


@dataclass
class Claim:
    kind: str
    text: str                 # the sentence, trimmed for display
    target: str = ""          # a path, a command or a package name
    hedged: bool = False      # "should pass now"
    extras: List[str] = field(default_factory=list)


_FENCE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`*\"'(\[])|\n+|;\s+|\s+but\s+|\s+however,?\s+", re.IGNORECASE)

_NEGATION = re.compile(
    r"\b(?:not|never|no longer|cannot|can't|couldn't|didn't|don't|doesn't|haven't|hasn't|hadn't|isn't|aren't"
    r"|wasn't|weren't|won't|wouldn't|unable|failed to|fails to)\b",
    re.IGNORECASE,
)
_NOT_A_CLAIM = re.compile(
    r"(?:\?\s*$|^\s*(?:to\s+\w+|run|try|please|if|consider|make sure|remember)\b"
    r"|\b(?:you|we)\s+(?:can|could|should|may|might|need|will|must|want)\b|\bif you\b|\bonce you\b|\bafter you\b"
    r"|\b(?:i'll|i will|i can|i could|let me|let's|i plan|i'm going to|i am going to|going to|plan to|need to|needs to|have to|in order to)\b"
    r"|\b(?:will|would)\s+(?:need|pass|work|build|run|be)\b|\bnext steps?\b|\btodo\b|\bto do\b)",
    re.IGNORECASE,
)
_HEDGE = re.compile(r"\b(?:should|ought to|probably|likely|presumably|i think|i believe|expected to)\b", re.IGNORECASE)

_PATH = re.compile(
    r"`([^`\s]+)`"
    r"|(?<![\w/.~-])((?:~?/)?(?:[\w.-]+/)*[\w.-]+\.(?:py|pyi|js|jsx|mjs|cjs|ts|tsx|go|rs|java|kt|kts|rb|php|cs|c|h|cc|cpp|hpp"
    r"|swift|m|mm|scala|sh|bash|zsh|ps1|sql|md|mdx|rst|txt|json|jsonc|yaml|yml|toml|ini|cfg|conf|lock|xml|html|htm"
    r"|css|scss|sass|less|vue|svelte|astro|tf|proto|graphql|gradle|dockerfile|ipynb|csv))(?![\w/])"
    r"|(?<![\w/.~-])((?:~?/)?(?:[\w.-]+/)+[\w.-]+)(?![\w/])"
    r"|\b(Dockerfile|Makefile|Procfile|Gemfile|Rakefile|Justfile|CODEOWNERS|LICENSE|README)\b",
)
PATTERNS = {
    "tests": re.compile(
        r"(?:\b(?:tests?|test suite|specs?|suite|pytest|jest|vitest|cargo test|go test|npm test|unit tests?|ci)\b[^.]{0,40}?"
        r"\b(?:pass(?:es|ed|ing)?|green|succeed(?:s|ed)?|all good|ok)\b"
        r"|\ball\s+(?:\d+\s+)?(?:checks|tests?)\s+(?:are\s+)?(?:green|passing|pass)\b"
        r"|\b\d+\s+(?:tests?\s+)?passed\b|\bno (?:test )?failures\b|\btests? (?:are|is) (?:all )?(?:green|passing)\b)",
        re.IGNORECASE,
    ),
    "build": re.compile(
        r"(?:\bbuild\b[^.]{0,30}?\b(?:pass(?:es|ed|ing)?|succeed(?:s|ed)?|works|worked|is green|is clean|completes|completed|ok)\b"
        r"|\b(?:builds|built|compiles|compiled)\s+(?:cleanly|successfully|fine|without (?:any )?errors)\b"
        r"|\bcompiles\b(?!\s+(?:with|but))|\bbuild is (?:green|clean|passing)\b)",
        re.IGNORECASE,
    ),
    "lint": re.compile(
        r"(?:\b(?:lint(?:er|ing)?|type[- ]?check(?:s|er|ing)?|types|mypy|ruff|eslint|flake8|pyright|tsc|clippy)\b[^.]{0,30}?"
        r"\b(?:pass(?:es|ed)?|clean|green|happy|no (?:errors|issues|warnings|problems))\b"
        r"|\bno (?:lint|type|typing|linter) (?:errors|issues|warnings)\b)",
        re.IGNORECASE,
    ),
    "edit": re.compile(
        r"\b(?:i(?:'ve| have)?\s+)?(?:created|wrote|written|added|updated|modified|edited|changed|fixed|patched|refactored"
        r"|rewrote|rewritten|renamed|moved|deleted|removed|replaced|implemented|saved)\b",
        re.IGNORECASE,
    ),
    "ran": re.compile(
        r"\b(?:i\s+)?(?:ran|run|executed|re-ran|reran|invoked)\b\s*`([^`]+)`|\brunning\s+`([^`]+)`\s+(?:shows|gives|returns|prints|reports)",
        re.IGNORECASE,
    ),
    "commit": re.compile(
        r"\b(?:committed|(?:made|created) (?:a |the )?commit|changes are committed|commit(?:ted)? (?:the )?(?:changes|fix|work))\b",
        re.IGNORECASE,
    ),
    "push": re.compile(
        r"\b(?:pushed|push(?:ed)? (?:it |the (?:branch|changes|commits?) )?to (?:origin|remote|github|gitlab|the remote|main|master|the branch))\b",
        re.IGNORECASE,
    ),
    "install": re.compile(
        r"\b(?:installed|added (?:the )?(?:dependency|dependencies|package)|dependencies (?:are )?(?:now )?installed)\b",
        re.IGNORECASE,
    ),
    "deploy": re.compile(
        r"\b(?:deployed|shipped to (?:prod|production|staging)|(?:is|are) (?:now )?live (?:at|on)|released to (?:prod|production))\b",
        re.IGNORECASE,
    ),
    "verified": re.compile(
        r"\b(?:i(?:'ve| have)?\s+)(?:tested|verified|confirmed|checked|validated|double-checked)\b"
        r"|\b(?:tested|verified|confirmed)\s+(?:that\s+)?(?:it|this|the (?:fix|change))\s+works\b"
        r"|\bworks (?:as expected|correctly|now)\b",
        re.IGNORECASE,
    ),
}
_NOT_PACKAGES = {"the", "a", "an", "all", "it", "them", "this", "that", "everything", "dependencies",
                 "deps", "packages", "requirements", "and", "with", "via", "from", "using", "locally"}
_PACKAGE = re.compile(r"\binstalled\s+`?([A-Za-z0-9@][\w@/.\-\[\]=<>~^]*)`?", re.IGNORECASE)


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" -*•>\t")
    return text if len(text) <= 160 else text[:159] + "…"


def split_sentences(reply: str) -> List[str]:
    body = _FENCE.sub(" ", reply or "")
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(">") or stripped.startswith("|"):
            continue  # quotes and table rows are not first-hand claims
        lines.append(line)
    parts = _SENTENCE_SPLIT.split("\n".join(lines))
    return [p.strip() for p in parts if p and p.strip()]


def _paths_in(sentence: str) -> List[tuple]:
    """Return (path, start offset) pairs for path-like tokens."""
    found: List[tuple] = []
    seen = set()
    for match in _PATH.finditer(sentence):
        token = next((g for g in match.groups() if g), "")
        token = token.strip("`'\".,:;()[]")
        if not token or " " in token or "://" in token or token.startswith("www."):
            continue
        if match.group(1) and not (("." in token.strip(".") and not token.endswith("()")) or "/" in token):
            continue  # a backticked word such as `foo` or `run()` is not a path
        if match.group(1) and re.fullmatch(r"[\w-]+\.[\w-]+\(.*\)?|v?\d+(?:\.\d+)+", token):
            continue  # `obj.method()` or a version number
        if token not in seen:
            seen.add(token)
            found.append((token, match.start()))
    return found


def extract_claims(reply: str) -> List[Claim]:
    claims: List[Claim] = []
    for sentence in split_sentences(reply):
        if _NOT_A_CLAIM.search(sentence) or _NEGATION.search(sentence):
            continue
        hedged = bool(_HEDGE.search(sentence))
        text = _clean(sentence)

        for kind in ("tests", "build", "lint", "commit", "push", "deploy"):
            if PATTERNS[kind].search(sentence):
                claims.append(Claim(kind=kind, text=text, hedged=hedged))

        if PATTERNS["install"].search(sentence):
            pkg = _PACKAGE.search(sentence)
            name = pkg.group(1) if pkg else ""
            if name.lower() in _NOT_PACKAGES:
                name = ""
            claims.append(Claim(kind="install", text=text, target=name, hedged=hedged))

        for match in PATTERNS["ran"].finditer(sentence):
            command = (match.group(1) or match.group(2) or "").strip()
            if command:
                claims.append(Claim(kind="ran", text=text, target=command, hedged=hedged))

        verbs = [m.end() for m in PATTERNS["edit"].finditer(sentence)]
        if verbs:
            for path, start in _paths_in(sentence):
                # The path must follow an edit verb closely: "I updated `a.py`",
                # "Fixed the import in src/b.ts". A path that appears far from
                # the verb is most likely a mention, not the object.
                if any(0 <= start - v <= 80 for v in verbs):
                    claims.append(Claim(kind="edit", text=text, target=path, hedged=hedged))

        verified = PATTERNS["verified"].search(sentence)
        if verified and not any(c.text == text and c.kind in ("tests", "build", "lint", "ran") for c in claims):
            # "tested" needs something that ran; "checked" or "confirmed" can rest on a read.
            needs_run = bool(re.search(r"\b(?:tested|validated|works)\b", verified.group(0), re.IGNORECASE))
            claims.append(Claim(kind="verified", text=text, hedged=hedged, extras=["run" if needs_run else "look"]))

    return _dedupe(claims)


def _dedupe(claims: List[Claim]) -> List[Claim]:
    seen = set()
    out: List[Claim] = []
    for claim in claims:
        key = (claim.kind, claim.target.lower(), claim.text if not claim.target else "")
        if key in seen:
            continue
        seen.add(key)
        out.append(claim)
    return out
