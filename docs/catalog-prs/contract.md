## What does this PR do?

Adds **Contract** to the plugin catalog (`plugin-catalog/contract.yaml`), pinned to
`vdimarco/hermes-plugin-hackathon@a3e6c9aa8d2033382400fb94db26892c45fdc643`, subdir `plugins/contract`.

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

**Hermes Desktop:** the package ships a `desktop/plugin.js` half (SDK imports only: `@hermes/plugin-sdk`, `react/jsx-runtime`). It adds a Contract pane with the live checklist (pass, fail, stale, not run), the locks and the receipt hash, a status-bar chip, a toast when the lock blocks an edit, and a palette command to copy the receipt hash.

**Hermes surfaces used:** hooks `pre_tool_call`, `post_tool_call`,
`pre_llm_call`, `pre_verify`, `transform_llm_output`, `on_session_delete`,
`subagent_start`; tools `contract_propose`, `contract_status`, `contract_run`;
slash command `/contract`; CLI command `hermes contract`;
`ctx.dispatch_tool("terminal", ...)` for check runs.

**Disclosure (rule 13):**
* **Desktop events:** sends plugin.contract.state snapshots (title, checks with commands and reasons, locks, receipt hash) and plugin.contract.blocked events to Desktop windows attached to the same Hermes process; the desktop half keeps the last 20 snapshots in Desktop plugin storage.
* No network calls, no credentials, no telemetry, no background threads.
* **Shell commands:** `contract_run` runs the approved check commands, plus one
  read-only git command for the receipt (`rev-parse`, `stash create`, `status`),
  through the Hermes terminal tool, so approvals and the configured terminal
  backend apply. The plugin never starts a process itself.
* Stores contracts (checks, locks, latest results with the last 400 characters of
  output) and the last 20 receipts in its plugin state under `HERMES_HOME/plugin-data`.
  `hermes contract export --out FILE` writes a receipt to the file the user names.
* Blocks tool calls that would change locked paths (it never approves anything),
  adds a one-line context note per turn while a contract is open, and appends one
  status line to replies (configurable).
* Rule 12: an agent's proposal is enforced only after the user's
  `/contract approve`. The gate is bounded by `max_nudges` and
  `agent.max_verify_nudges`, so unattended runs cannot hang on it.

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

The plugin repo's own suite (`python -m unittest discover -s tests -t . -v`) runs
in GitHub Actions on Python 3.11 to 3.13, including an integration test that checks
the receipt's tree id against a real commit.

## Checklist

### Code

- [ ] I've read the [Contributing Guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md)
- [x] My commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
- [x] I searched for [existing PRs](https://github.com/NousResearch/hermes-agent/pulls) to make sure this isn't a duplicate
- [x] My PR contains **only** changes related to this fix/feature (one catalog file)
- [ ] I've run `hermes plugins validate` on the pinned commit and it passes
- [ ] I've tested on my platform: <!-- e.g. macOS 15 -->

### Documentation & Housekeeping

- [x] Documentation: the plugin README (rendered from the pinned commit) covers use, the receipt, configuration, disclosure and limits
- [x] N/A: `cli-config.yaml.example`, `CONTRIBUTING.md`, `AGENTS.md`
- [x] Cross-platform: the plugin is pure Python; check commands are the user's own

## Screenshots / Logs

<!-- Add a screenshot of the "Contract lock" block, the "Contract met" footer, and `hermes contract verify`. -->
