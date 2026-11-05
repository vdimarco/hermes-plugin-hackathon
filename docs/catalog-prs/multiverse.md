## What does this PR do?

Adds **Multiverse** to the plugin catalog (`plugin-catalog/multiverse.yaml`), pinned to
`vdimarco/hermes-plugin-hackathon@3564b40263fcceb20c60c3b2f0b86b87416f181e`, subdir `plugins/multiverse`.

Multiverse is best-of-N for coding agents, with real checks. `multiverse_race`:

1. makes one git worktree per attempt (2 to 6) from the same base: `HEAD`, or the
   uncommitted tracked changes through `git stash create`, which does not touch the
   user's tree or stash list;
2. starts one `delegate_task` subagent per worktree, each with its own approach;
3. pins each subagent to its worktree: `pre_tool_call` rewrites the terminal
   `workdir`, file paths and V4A patch paths into the worktree, and blocks access to
   the other worktrees and nested delegation;
4. runs the same check commands in every worktree and returns a scoreboard, ranked
   by checks passed, then a real change, then the smallest diff;
5. `multiverse_adopt` applies the winner with `git apply` and removes the worktrees.

With `delegation.backend: multiverse`, each attempt can use its own model and
provider, so a race compares models as well as approaches. Tasks without a
Multiverse marker take the default route.

**Hermes Desktop:** the package ships a `desktop/plugin.js` half (SDK imports only: `@hermes/plugin-sdk`, `react/jsx-runtime`). It adds a live race board pane (each attempt's status and tool-call count, then checks passed, diff and the winner), a status-bar chip while racing, and a toast when judging ends.

**Hermes surfaces used:** hooks `subagent_start`, `subagent_stop`,
`pre_tool_call` (`modify` and `block`); tools `multiverse_race`,
`multiverse_open`, `multiverse_judge`, `multiverse_adopt`, `multiverse_close`;
a delegation provider (`multiverse`); slash command `/multiverse`; CLI command
`hermes multiverse`; `ctx.dispatch_tool` for `terminal` and `delegate_task`.

**Disclosure (rule 13):**
* **Desktop events:** sends plugin.multiverse.run snapshots (goal, checks, per-attempt label, model, approach, status, tool-call count, scores and diff stats; no subagent text) to Desktop windows attached to the same Hermes process, at most once a second while attempts run; the desktop half keeps the last 10 runs in Desktop plugin storage.
* No network calls of its own, no credentials, no telemetry, no background threads.
* **Shell commands:** runs `git` (`rev-parse`, `stash create`,
  `worktree add/remove/prune`, `add -A` inside the worktrees, `diff`, `apply`),
  `mkdir`, `rm -f` of its own `.patch` files and `rmdir`, plus the user's check
  commands, through the Hermes terminal tool, so approvals and the configured
  terminal backend apply.
* **Files outside the plugin's data:** creates worktrees next to the repository in
  `.<repo>.multiverse/<run id>/`; `multiverse_adopt` changes the user's working
  tree (only when called).
* **Cost:** starts subagents through `delegate_task`, so each attempt uses tokens
  on the model it runs on.
* Stores run metadata (goal, checks, worktree paths, scores, the last 1500
  characters of each subagent summary) in its plugin state under `HERMES_HOME/plugin-data`.

## Related Issue

N/A (Hermes Agent Plugin Hackathon entry).

## Type of Change

- [x] ✨ New feature (non-breaking change that adds functionality)

## Changes Made

- `plugin-catalog/multiverse.yaml`: new community entry (category `tools`,
  `platforms: [linux, macos]`, `requires_hermes: ">=0.22.0"`, the same floor as in `plugin.yaml`).

## How to Test

1. `hermes plugins validate <clone>/plugins/multiverse --install-deps`
2. `hermes plugins install https://github.com/vdimarco/hermes-plugin-hackathon/tree/main/plugins/multiverse && hermes plugins enable multiverse`
3. In the plugin repo's `demo/cart` (committed to git): ask *"Race three fixes for the double discount in cart.py; the check is `pytest -q`."* The reply shows a scoreboard with 🏆. Ask the agent to adopt the winner; `git diff` then shows only the winner's change.

The plugin repo's own suite runs in GitHub Actions on Python 3.11 to 3.13,
including integration tests that run the real git commands in a temporary repo
(worktrees, pinned writes, judging, `git apply`, cleanup).

## Checklist

### Code

- [ ] I've read the [Contributing Guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md)
- [x] My commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
- [x] I searched for [existing PRs](https://github.com/NousResearch/hermes-agent/pulls) to make sure this isn't a duplicate
- [x] My PR contains **only** changes related to this fix/feature (one catalog file)
- [ ] I've run `hermes plugins validate` on the pinned commit and it passes
- [ ] I've tested on my platform: <!-- e.g. macOS 15 -->

### Documentation & Housekeeping

- [x] Documentation: the plugin README (rendered from the pinned commit) covers how it works, model races, disclosure and limits
- [x] N/A: `cli-config.yaml.example`, `CONTRIBUTING.md`, `AGENTS.md`
- [x] Cross-platform: Linux and macOS (declared in `platforms`); Windows needs a POSIX shell

## Screenshots / Logs

<!-- Add a screenshot of the scoreboard and of `git worktree list` during a race. -->
