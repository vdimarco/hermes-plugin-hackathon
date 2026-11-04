"""Tool schemas: what the model sees."""

_CHECK = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Short name, for example 'unit tests' or 'health endpoint'."},
        "command": {
            "type": "string",
            "description": "Shell command that proves part of the task is done, for example 'pytest -q tests/test_cart.py'. "
                           "It must be deterministic and must not change files.",
        },
        "expect_exit": {"type": "integer", "description": "Exit code that means pass. Default 0."},
        "expect_output": {"type": "string", "description": "Optional regex the output must match, for example '200 OK'."},
        "forbid_output": {"type": "string", "description": "Optional regex the output must not match, for example 'TODO|FIXME'."},
        "workdir": {"type": "string", "description": "Optional folder to run the command in."},
    },
    "required": ["command"],
}

CONTRACT_PROPOSE = {
    "name": "contract_propose",
    "description": (
        "Before you start a coding task, write its definition of done as a contract: a few shell checks that pass "
        "only when the task is done, plus files that must not change (usually the tests). The user approves the "
        "contract with /contract approve. After approval the checks and locked files cannot change, and you cannot "
        "finish the task until every check passes. Prefer existing tests; add a check for each requirement the "
        "user stated. If a test for the new behaviour does not exist yet, write it before you propose the contract."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The task in a few words."},
            "checks": {"type": "array", "minItems": 1, "maxItems": 12, "items": _CHECK},
            "locked_paths": {
                "type": "array", "items": {"type": "string"},
                "description": "Paths or globs that must not change, for example 'tests/test_cart.py'. "
                               "Files named in check commands and common test paths are locked automatically.",
            },
        },
        "required": ["title", "checks"],
    },
}

CONTRACT_STATUS = {
    "name": "contract_status",
    "description": "Show the contract for this session: each check with its latest result (pass, fail, stale or pending) and the locked paths.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

CONTRACT_RUN = {
    "name": "contract_run",
    "description": (
        "Run the contract checks through the terminal and record the results. When every check passes, "
        "this writes the receipt that proves the task is done. Call it after your last edit."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "check": {"type": "string", "description": "Optional check id (c1, c2, ...) to run only one check."},
        },
        "required": [],
    },
}
