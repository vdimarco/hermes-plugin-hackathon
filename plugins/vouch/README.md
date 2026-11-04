# Vouch

**The agent's "done" must match the tool record.**

Vouch is a Hermes Agent plugin. It reads the agent's final reply, finds claims of
finished work, and checks each claim against what the agent really did in the
session. A false claim does not reach you as fact.

```
Fixed the discount rounding in `src/cart.py`. All tests pass and I pushed the fix to origin.

---
Vouch · 3 claims checked · 1 contradicted · 1 unverified · 1 verified
- ❌ "tests pass": the last test run, `pytest -q | tail -2` exited 0, but the output
  shows failures: "FAILED tests/test_cart.py::test_discount - assert 90 == 81"
- ⚠️ "changes pushed": no git push command ran in this session
```

In nudge mode (the default), you do not see that reply. Vouch sends the agent
back first, with the exact mismatch, and the agent fixes the work or the words.

## Why a plugin, not a prompt

A prompt can ask the model to be honest. A prompt cannot see the exit code.

Vouch sits on the tool loop. It records every tool call through `post_tool_call`,
so it knows which commands ran, how they exited, what their output said, and which
files changed and when. It then checks the reply in two places:

| Hook | When | What Vouch does |
|---|---|---|
| `pre_verify` | The agent edited code and is about to finish | If a claim is contradicted, stale or unsupported, Vouch keeps the turn going and tells the agent what the record shows. Bounded by `max_nudges` (default 1) and by Hermes' `agent.max_verify_nudges`. |
| `transform_llm_output` | The final reply, on every turn | Claims that are still unsupported get a short footer. Replies with only verified claims are not changed. |

Hermes has a built-in verify-on-stop nudge that asks for verification after
edits. Vouch is different: it reads *what the agent says* and compares each
statement to the evidence. It also works on turns with no edits ("I pushed it",
"it's deployed"), and it catches failures that look like passes.

## What it catches

| Claim in the reply | Evidence Vouch looks for | Typical catch |
|---|---|---|
| "all tests pass", "24 tests passed", "CI is green" | The latest test run (pytest, jest, vitest, go test, cargo test, npm test, and about 30 more) | The run failed. The exit code was hidden by a pipe (`pytest \| tail`). The runner found no tests. Files changed after the last pass. |
| "the build succeeds", "compiles cleanly" | The latest build run | Build failed, or files changed after it. |
| "lint is clean", "no type errors" | The latest lint or type-check run (ruff, eslint, mypy, tsc --noEmit, clippy, ...) | Same as above. |
| "I updated `src/app.py`" | A `write_file` or `patch` on that path, or a shell write (`>`, `sed -i`, `mv`, ...) | No write to that file. The file was only read. Every write failed. |
| "I ran `make lint`" | A terminal call with that command | The command never ran. |
| "committed", "pushed to origin" | A clean `git commit` / `git push` | Push was rejected. Edits came after the commit. |
| "installed `httpx`" | An install command that names the package | Some other package was installed. |
| "deployed to production" | A deploy command (vercel, fly, wrangler, kubectl apply, terraform apply, ...) | No deploy ran, or it failed. |
| "I tested it", "works as expected" | Something ran after the last edit | Nothing ran after the last change. |

Verdicts: ✅ verified · ⚠️ unverified · ⏳ stale (it passed, then files changed) · ❌ contradicted.

The extractor skips text that is not a first-hand claim: questions, plans
("I will push"), instructions ("run `pytest` to check"), negations ("I did not
run the tests"), code blocks, quotes and tables. "Should pass now" is kept and
marked as hedged.

## Install

```bash
hermes plugins install https://github.com/go-montreal-go/start-here/tree/main/plugins/vouch
hermes plugins enable vouch
```

Or, for local work, copy or link this folder to `~/.hermes/plugins/vouch/` and
run `hermes plugins enable vouch`.

## Use

Nothing to do. Work with Hermes as usual. Then:

| Command | What it shows |
|---|---|
| `/vouch` | The verdicts for the last reply in this session |
| `/vouch evidence` | The tool record Vouch checks against |
| `/vouch stats` | Totals across sessions |
| `hermes vouch stats` | Totals from the shell: claims checked, flags by type, how often the agent was sent back |
| `hermes vouch log` | The last 50 flagged claims as JSON |
| `hermes vouch reset` | Clear the totals |

The model also gets two tools:

* `vouch_evidence`: the session record, filtered by `tests`, `build`, `lint`,
  `edits`, `git`, `install`, `deploy` or `failures`.
* `vouch_check`: check a draft reply before sending it.

## Configure

Settings live under `plugins.entries.vouch.settings` in `config.yaml`:

```yaml
plugins:
  entries:
    vouch:
      settings:
        mode: nudge          # nudge | annotate | off
        max_nudges: 1        # times one turn can be sent back
        footer: true         # footer under replies with unsupported claims
        footer_verified: false  # also list verified claims (good for demos)
        kinds: []            # limit to some claim types, empty = all
```

* `nudge`: send the agent back after code edits, then annotate what is left.
* `annotate`: never send the agent back, only add the footer.
* `off`: record and keep totals, change nothing.

## Privacy and safety disclosure

* No network calls, no shell commands, no subprocesses, no background threads,
  no credentials, no telemetry.
* In memory, per session (at most 64 sessions, 400 events each): tool name,
  command text, file paths, exit code, and the last 600 characters of command
  output. Vouch never stores file contents. The record is dropped when Hermes
  deletes the session or the process stops.
* On disk, in the plugin state under `HERMES_HOME/plugin-data/`: totals by claim
  type and the last 50 flagged claims (the claim sentence, up to 160 characters,
  and the reason).
* It changes the reply only by appending a footer, and only when a claim is not
  supported. It does not block tools, approve anything, or override Hermes core.

## Limits

* Claim detection uses patterns, tuned for English. It misses some claims. It
  prefers a missed claim to a false alarm.
* Evidence is what passed through Hermes tools in this session. Work done in an
  earlier session, by a subagent, or by hand is not on record, so a true claim
  about it reads as unverified.
* "Tests pass" is judged on the latest test run in the session. If you run two
  test suites, the latest one decides.

## Develop

```bash
# from the repository root
python -m unittest discover -s tests -t . -v     # needs PyYAML
hermes plugins doctor plugins/vouch --ci
hermes plugins validate plugins/vouch --install-deps
```

`demo/cart/` at the repository root is a tiny project with one bug, for trying
Vouch by hand. See [`docs/demo.md`](https://github.com/go-montreal-go/start-here/blob/main/docs/demo.md).

Vouch is part of a set with [Contract](https://github.com/go-montreal-go/start-here/tree/main/plugins/contract) and
[Multiverse](https://github.com/go-montreal-go/start-here/tree/main/plugins/multiverse).

## License

MIT
