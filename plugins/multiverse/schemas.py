"""Tool schemas: what the model sees."""

import copy

_ATTEMPTS = {
    "type": "array",
    "minItems": 2,
    "maxItems": 6,
    "description": (
        "Optional list of attempts. Each attempt can set a label, an approach (a one-line strategy that makes it "
        "different from the others) and a model and provider (used when delegation.backend is 'multiverse')."
    ),
    "items": {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "approach": {"type": "string"},
            "model": {"type": "string"},
            "provider": {"type": "string"},
        },
    },
}

MULTIVERSE_RACE = {
    "name": "multiverse_race",
    "description": (
        "Attempt a hard coding task several ways at once and keep the best result. Multiverse makes one git worktree "
        "per attempt, starts one subagent in each (pinned to its worktree), runs the same checks in every worktree, "
        "and returns a scoreboard: checks passed, then the smallest diff. Use it when the right fix is unclear, or "
        "when the user asks to compare approaches or models. Then call multiverse_adopt to apply the winner. "
        "Commit or stash first if you want the attempts to start from a clean tree; uncommitted tracked changes are "
        "included in the base."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "The task, complete and self-contained. The subagents see nothing else."},
            "checks": {"type": "array", "minItems": 1, "items": {"type": "string"},
                       "description": "Shell commands that pass only when the task is done, for example 'pytest -q'."},
            "count": {"type": "integer", "minimum": 2, "maximum": 6,
                      "description": "Number of attempts when you do not give a list. Default 3."},
            "attempts": _ATTEMPTS,
            "repo": {"type": "string", "description": "Folder inside the git repository. Default: the current folder."},
        },
        "required": ["goal", "checks"],
    },
}

MULTIVERSE_OPEN = {
    "name": "multiverse_open",
    "description": (
        "Step 1 of a manual race: make the worktrees and return the delegate_task tasks to start. "
        "Use multiverse_race instead unless you need to change the tasks."
    ),
    "parameters": copy.deepcopy(MULTIVERSE_RACE["parameters"]),
}

MULTIVERSE_JUDGE = {
    "name": "multiverse_judge",
    "description": "Run the checks in every universe of a run, measure each diff, and return the scoreboard.",
    "parameters": {
        "type": "object",
        "properties": {"run": {"type": "string", "description": "Run id. Default: the latest run."}},
        "required": [],
    },
}

MULTIVERSE_ADOPT = {
    "name": "multiverse_adopt",
    "description": "Apply one universe's changes to the real working tree (default: the winner), then remove the worktrees.",
    "parameters": {
        "type": "object",
        "properties": {
            "run": {"type": "string", "description": "Run id. Default: the latest run."},
            "universe": {"type": "string", "description": "Universe label. Default: the judged winner."},
            "keep_worktrees": {"type": "boolean", "description": "Keep the worktrees for inspection. Default false."},
        },
        "required": [],
    },
}

MULTIVERSE_CLOSE = {
    "name": "multiverse_close",
    "description": "Remove the worktrees of a run without applying anything.",
    "parameters": {
        "type": "object",
        "properties": {"run": {"type": "string", "description": "Run id. Default: the latest run."}},
        "required": [],
    },
}
