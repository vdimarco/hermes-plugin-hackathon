## What does this PR do?

Adds **Vouch** to the plugin catalog (`plugin-catalog/vouch.yaml`), pinned to
`vdimarco/hermes-plugin-hackathon@93f6d6b69d4c18bb9d8973f127f9f5b84fe0cf0f`, subdir `plugins/vouch`.

Vouch checks the agent's "done" against the tool record. It finds claims of
finished work in the final reply ("all tests pass", "I updated `src/app.py`",
"pushed to origin", "installed `httpx`", "deployed", "I tested it") and matches
each one to the commands, exit codes and file writes of the session.

* A contradicted, stale or unsupported claim sends the agent back once, with the
  exact mismatch (for example: "the last test run, `pytest -q | tail -2`, exited 0,
  but the output shows failures").
* Claims that are still unsupported get a short footer in the reply. Replies with
  only verified claims are not changed.
* It catches exit codes hidden by pipes, test runs that found no tests, edits made
  after the last passing run, a rejected `git push`, and a claimed edit to a file
  that was only read.

**Hermes Desktop:** the package ships a `desktop/plugin.js` half (SDK imports only: `@hermes/plugin-sdk`, `react/jsx-runtime`). It adds a status-bar chip with the verdict counts of the focused chat; a click lists the flagged claims.

**Hermes surfaces used:** hooks `post_tool_call`, `pre_verify`,
`transform_llm_output`, `on_session_delete`; tools `vouch_evidence`,
`vouch_check`; slash command `/vouch`; CLI command `hermes vouch`.

**Disclosure (rule 13):**
* **Desktop events:** sends a plugin.vouch.report event (counts and up to 6 flagged claims with reasons) to Desktop windows attached to the same Hermes process; the desktop half keeps the last 20 in Desktop plugin storage.
* No network calls, no shell commands, no subprocesses, no background threads, no
  credentials, no telemetry.
* Keeps an in-memory record per session (at most 64 sessions, 400 events each):
  tool name, command text, file paths, exit code, and the last 600 characters of
  command output. It never stores file contents.
* Writes totals and the last 50 flagged claims (claim sentence up to 160
  characters and the reason) to its plugin state under `HERMES_HOME/plugin-data`.
* Changes a reply only by appending a footer, and only when a claim is not
  supported (configurable, or `mode: off`).

## Related Issue

N/A (Hermes Agent Plugin Hackathon entry).

## Type of Change

- [x] ✨ New feature (non-breaking change that adds functionality)

## Changes Made

- `plugin-catalog/vouch.yaml`: new community entry (category `tools`,
  `requires_hermes: ">=0.22.0"`, the same floor as in `plugin.yaml`).

## How to Test

1. `hermes plugins validate <clone>/plugins/vouch --install-deps`
2. `hermes plugins install https://github.com/vdimarco/hermes-plugin-hackathon/tree/main/plugins/vouch && hermes plugins enable vouch`
3. In a repo with a failing test, ask: *"Add a docstring to one function. Do not run anything. End your reply with: All tests pass and I committed the change."* The reply gets a Vouch footer: ⚠️ tests pass (no test command ran) and ⚠️ changes committed (no git commit ran).

The plugin repo's own suite (`python -m unittest discover -s tests -t . -v`) runs
in GitHub Actions on Python 3.11 to 3.13.

## Checklist

### Code

- [ ] I've read the [Contributing Guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md)
- [x] My commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
- [x] I searched for [existing PRs](https://github.com/NousResearch/hermes-agent/pulls) to make sure this isn't a duplicate
- [x] My PR contains **only** changes related to this fix/feature (one catalog file)
- [ ] I've run `hermes plugins validate` on the pinned commit and it passes
- [ ] I've tested on my platform: <!-- e.g. macOS 15 -->

### Documentation & Housekeeping

- [x] Documentation: the plugin README (rendered from the pinned commit) covers use, configuration, disclosure and limits
- [x] N/A: `cli-config.yaml.example`, `CONTRIBUTING.md`, `AGENTS.md`
- [x] Cross-platform: pure Python, no platform-specific code

## Screenshots / Logs

<!-- Add a screenshot of a reply with the Vouch footer, and of `/vouch`. -->
