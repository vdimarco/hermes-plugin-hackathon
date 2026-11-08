## What does this PR do?

Adds **Contract** to the plugin catalog (`plugin-catalog/contract.yaml`), pinned to
`vdimarco/hermes-plugin-hackathon@44a513dc0eae7f25a9bd063572f689cf0a17eaeb`, subdir `plugins/contract`.

Contract is a definition of done the agent cannot fake. Before a coding task, the
agent proposes shell checks and locked paths (usually the tests); the user
approves them with `/contract approve`. From then on:

* Writes to locked paths are blocked: `write_file`, `patch` (also V4A bodies),
  shell writes (`>`, `tee`, `sed -i`, `rm`, `mv`, `cp` into, `git checkout`/`restore`)
  and file writes in `execute_code`. Subagents the session starts inherit the locks.
* A turn that edited code cannot finish while a check fails, has not run, or
  passed before the last edit. A pipe that hides a failure still counts as a failure.
* When every check passes, `contract_run` writes a receipt: a hash-chained JSON
  record of exit codes and output hashes, plus the git tree id of the checked
  code. `/contract receipt` prints a PR block; `hermes contract verify` checks the
  hashes and `--script` prints a script that runs the checks again.

Contract also ships **Vouch, a claim check** that runs on every turn, with or
without a contract. It finds claims of finished work in the final reply ("all
tests pass", "I updated `src/app.py`", "pushed to origin", "installed `httpx`",
"deployed") and matches each one to the commands, exit codes and file writes of
the session.

* A contradicted, stale or unsupported claim sends the agent back once, with the
  exact mismatch (for example: "the last test run, `pytest -q | tail -2`, exited 0,
  but the output shows failures").
* It catches exit codes hidden by pipes, test runs that found no tests, edits made
  after the last passing run, a rejected `git push`, and a claimed edit to a file
  that was only read.
* Claims that are still unsupported get a short footer. The same footer carries the
  contract's status line, because Hermes keeps only the first reply rewrite.

**Hermes Desktop:** the package ships a `desktop/plugin.js` half (SDK imports only: `@hermes/plugin-sdk`, `react/jsx-runtime`). It adds a Contract pane with the live checklist (pass, fail, stale, not run), the locks and the receipt hash, a status-bar chip, a second chip with the claim verdicts of the latest reply (a click lists the flagged claims), toasts when the lock blocks an edit or when the claim check sends the agent back, and a palette command to copy the receipt hash.

**Hermes surfaces used:** hooks `pre_tool_call`, `post_tool_call`,
`pre_llm_call`, `pre_verify`, `transform_llm_output`, `on_session_delete`,
`subagent_start`; tools `contract_propose`, `contract_status`, `contract_run`,
`vouch_evidence`, `vouch_check`; slash commands `/contract`, `/vouch`; CLI
commands `hermes contract`, `hermes vouch`; `ctx.dispatch_tool("terminal", ...)`
for check runs.

**Disclosure (rule 13):**
* **Desktop events:** sends plugin.contract.state snapshots (title, checks with commands and reasons, locks, receipt hash), plugin.contract.blocked events and plugin.contract.report events (claim counts and up to 6 flagged claims with reasons) to Desktop windows attached to the same Hermes process; the desktop half keeps the last 20 snapshots and 20 reports in Desktop plugin storage.
* No network calls, no credentials, no telemetry, no background threads.
* **Shell commands:** `contract_run` runs the approved check commands, plus one
  read-only git command for the receipt (`rev-parse`, `stash create`, `status`),
  through the Hermes terminal tool, so approvals and the configured terminal
  backend apply. The plugin never starts a process itself.
* Keeps an in-memory tool record per session (at most 64 sessions, 400 events
  each): tool name, command text, file paths, exit code, and the last 600
  characters of command output. It never stores file contents.
* Stores contracts (checks, locks, latest results with the last 400 characters of
  output), the last 20 receipts, claim-check totals and the last 50 flagged claims
  (claim sentence up to 160 characters and the reason) in its plugin state under
  `HERMES_HOME/plugin-data`.
  `hermes contract export --out FILE` writes a receipt to the file the user names.
* Blocks tool calls that would change locked paths (it never approves anything),
  adds a one-line context note per turn while a contract is open, and appends a
  footer to replies only when a claim is unsupported or the contract has news
  (configurable; `claims_mode: off` turns the claim check off).
* Rule 12: an agent's proposal is enforced only after the user's
  `/contract approve`. The gate is bounded by `max_nudges`, the claim check by
  `claims_max_nudges`, and both by `agent.max_verify_nudges`, so unattended runs
  cannot hang on them.

## Related Issue

N/A (Hermes Agent Plugin Hackathon entry).

## Type of Change

- [x] ✨ New feature (non-breaking change that adds functionality)

## Changes Made

- `plugin-catalog/contract.yaml`: new community entry (category `tools`,
  `requires_hermes: ">=0.22.0"`, the same floor as in `plugin.yaml`).

## How to Test

1. `hermes plugins validate <clone>/plugins/contract --install-deps`
2. `hermes plugins install https://github.com/vdimarco/hermes-plugin-hackathon/tree/main/plugins/contract && hermes plugins enable contract`
3. In the plugin repo's `demo/cart` (one bug, one failing test): ask the agent to write a contract and fix the discount bug, run `/contract approve`, then ask it to change the expected value in `tests/test_cart.py`. The edit is blocked with "Contract lock". After the real fix, `contract_run` reports "met" and `/contract receipt` prints the PR block.
4. Claim check: ask *"Add a docstring to one function. Do not run anything. End your reply with: All tests pass and I committed the change."* The reply gets a Vouch footer: ⚠️ tests pass (no test command ran) and ⚠️ changes committed (no git commit ran).

The plugin repo's own suite (`python -m unittest discover -s tests -t . -v`) runs
in GitHub Actions on Python 3.11 to 3.13, including an integration test that checks
the receipt's tree id against a real commit.

## Checklist

### Code

- [ ] I've read the [Contributing Guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md)
- [x] My commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
- [x] I searched for [existing PRs](https://github.com/NousResearch/hermes-agent/pulls) to make sure this isn't a duplicate
- [x] My PR contains **only** changes related to this fix/feature (one catalog file)
- [x] I've run `hermes plugins validate` on the pinned commit and it passes
- [x] I've tested on my platform: Linux, Hermes at e075441 on Python 3.14 (smoke test: 25 of 25 checks pass)

### Documentation & Housekeeping

- [x] Documentation: the plugin README (rendered from the pinned commit) covers use, the receipt, the claim check, configuration, disclosure and limits
- [x] N/A: `cli-config.yaml.example`, `CONTRIBUTING.md`, `AGENTS.md`
- [x] Cross-platform: the plugin is pure Python; check commands are the user's own

## Screenshots / Logs

<!-- Add a screenshot of the "Contract lock" block, the "Contract met" footer, and `hermes contract verify`. -->
