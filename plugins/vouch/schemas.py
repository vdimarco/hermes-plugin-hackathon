"""Tool schemas: what the model sees."""

VOUCH_EVIDENCE = {
    "name": "vouch_evidence",
    "description": (
        "Show the evidence record for this session: the commands you ran with their exit codes, "
        "the files you wrote, and the test, build, lint, git, install and deploy runs. "
        "Call this before you tell the user that work is done, tests pass, or changes are committed, "
        "so that your report matches what really happened."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["all", "tests", "build", "lint", "edits", "git", "install", "deploy", "failures"],
                "description": "Which events to show. Default: all.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of events, newest last. Default: 20.",
            },
        },
        "required": [],
    },
}

VOUCH_CHECK = {
    "name": "vouch_check",
    "description": (
        "Check a draft reply against the evidence record before you send it. "
        "Returns each claim found in the text (tests pass, file changed, command ran, committed, "
        "pushed, installed, deployed, verified) with a verdict: verified, unverified, stale "
        "(the check passed but files changed after it) or contradicted (the record shows the opposite)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The draft reply to check."},
        },
        "required": ["text"],
    },
}
