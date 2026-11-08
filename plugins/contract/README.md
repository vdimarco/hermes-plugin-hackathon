# Contract

**The agent writes the exam before it takes it, cannot change the exam, and cannot
say it passed when it did not.**

Contract is a Hermes Agent plugin with two parts that read one tool record:

* **The contract.** "Done" becomes a short list of shell checks that you approve
  before the work starts. After that, the agent cannot edit the tests, cannot
  finish while a check fails, and gets a receipt that a reviewer can check when
  every check passes.
* **Vouch, the claim check.** On every turn, with or without a contract, Vouch
  reads the final reply, finds claims of finished work ("all tests pass",
  "pushed to origin", "I updated `src/app.py`") and checks each one against what
  the agent really ran. A false claim sends the agent back. What is still
  unsupported goes in the reply footer.

```
You:    The discount is applied twice in cart.py. Fix it.
Agent:  [contract_propose] "Fix double discount"
          c1  pytest -q tests/test_cart.py
          c2  python -c "import cart; assert cart.apply_discount(100, 10) == 90"
        🔒 tests/test_cart.py, tests/*, conftest.py, ...
---
📝 Contract proposed. Run /contract to read it and /contract approve to lock it.

You:    /contract approve
        🔒 Contract approved and locked.

Agent:  [patch tests/test_cart.py]  ✗ Contract lock: tests/test_cart.py is covered by the
                                      approved contract. Fix the code, not the checks.
        [patch cart.py] [contract_run]  c1 ✅  c2 ✅
---
✅ Contract met: "Fix double discount" · 2/2 checks pass · receipt 9f2c41d0a7e3
```

Without a contract, Vouch still checks the reply:

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

## Install

From a terminal:

```bash
hermes plugins install https://github.com/vdimarco/hermes-plugin-hackathon/tree/main/plugins/contract
hermes plugins enable contract
```

In Hermes Desktop, paste this link into your browser's address bar, or use
**Capabilities → Plugins → Install from Git** with the folder URL above. Hermes
shows what it found and asks before it installs anything.

```text
hermes://plugin/install?repo=https%3A%2F%2Fgithub.com%2Fvdimarco%2Fhermes-plugin-hackathon%2Ftree%2Fmain%2Fplugins%2Fcontract&enable=1
```

Once contract is listed in the Hermes plugin catalog, the catalog card's **Open in
Hermes Desktop** button does the same at the reviewed commit
(`hermes://plugin/install?catalog=contract`).

## Why a plugin, not a prompt

A prompt can ask the agent not to touch the tests. Contract makes it unable to.
A prompt can ask the model to be honest. A prompt cannot see the exit code.

| Hook | What Contract does |
|---|---|
| `pre_tool_call` | Blocks writes to locked paths: `write_file`, `patch` (also V4A bodies), shell writes (`>`, `tee`, `sed -i`, `rm`, `mv`, `cp` into, `git checkout`/`restore`), and file writes in `execute_code`. |
| `subagent_start` | Subagents started from the session inherit the locks, so a delegated helper or a Multiverse attempt cannot edit the tests either. |
| `post_tool_call` | Records every run of a check command with its exit code, also when the agent runs it with the terminal tool directly. A pipe that hides a failure (`pytest \| tail`) still counts as a failure. |
| `post_tool_call` (Vouch) | The same record holds every command with its exit code and output tail, and every file write. |
| `pre_verify` | A turn that edited code cannot finish while a check fails, has not run, or passed before the last edit. Bounded by `max_nudges` (default 2) and `agent.max_verify_nudges`. When the contract lets the turn end, Vouch checks the draft reply: a contradicted, stale or unsupported claim sends the agent back once (`claims_max_nudges`). |
| `pre_llm_call` | While a contract is open, adds a one-line reminder of the definition of done. |
| `transform_llm_output` | One footer: the claims Vouch could not verify, then one contract status line (proposed, 2/3 pass, or met with the receipt id). Replies with nothing to report are not changed. |

## What Vouch catches

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

## The receipt

When every check passes, Contract writes a receipt:

* the approved contract (checks, locks, who approved it) and its hash
* each passing run: command, exit code, SHA-256 of the output, time
* a hash chain over the runs, and a hash over the whole receipt
* the files changed since the contract was approved
* `head`, `dirty` and `tree`: the git tree id of the checked code, made with
  `git stash create` so that nothing in your repo changes. After you commit,
  `git rev-parse HEAD^{tree}` gives the same id if the commit is the code that
  was checked.

`/contract receipt` prints a block for the PR description with a check table
and the JSON. A reviewer runs:

```bash
hermes contract verify receipt.json            # are the hashes intact?
hermes contract verify receipt.json --script   # prints a bash script that re-runs every check
```

The hashes show if the receipt was edited. They are not a signature: someone who
can run the plugin can make a new receipt. Re-running the checks is the real
proof, and the script makes that one command.

## Commands

| Command | What it does |
|---|---|
| `/contract` | Status: each check (✅ pass, ❌ fail, ♻️ stale, ⏳ not run) and the locks |
| `/contract approve` | Lock the contract the agent proposed |
| `/contract add <command>` | Add a check yourself (a contract you write is approved at once) |
| `/contract lock <glob>` / `unlock <glob>` | Change the locks |
| `/contract receipt` | The PR block |
| `/contract reset` | Drop the contract |
| `hermes contract list\|show\|export\|verify` | Receipts from the shell |
| `/vouch` | Vouch's verdicts for the last reply in this session |
| `/vouch evidence` | The tool record that Vouch and the contract check against |
| `/vouch stats` | Claim-check totals across sessions |
| `hermes vouch stats\|log\|reset` | The same totals from the shell; `log` prints the last 50 flagged claims as JSON |

Model tools: `contract_propose`, `contract_status`, `contract_run`, and for the claim
check `vouch_evidence` (the session record, filtered by `tests`, `build`, `lint`,
`edits`, `git`, `install`, `deploy` or `failures`) and `vouch_check` (check a draft
reply before sending it).

## Hermes Desktop

The package ships a desktop half (`desktop/plugin.js`). In Hermes Desktop it adds:

* a **Contract** pane with the live checklist of the focused chat (✅ pass, ❌ fail,
  ♻️ stale, ⏳ not run), the locks, and the receipt hash with a copy button;
* a status-bar chip, for example `contract 2/3`;
* a second chip with Vouch's verdicts on the latest reply, for example
  `vouch ✓3 ⚠1 ✗1`. Click it to list the flagged claims;
* a toast when the lock blocks an edit, and one when Vouch sends the agent back;
* the palette command **Contract: copy receipt hash**.

Turn it on in **Capabilities → Plugins** (desktop halves ship off by default).

## Configure

```yaml
plugins:
  entries:
    contract:
      settings:
        require_approval: true   # false: an agent's proposal is enforced at once (unattended runs)
        lock_tests: true         # also lock tests/*, test_*.py, *.test.*, *.spec.*, conftest.py
        max_nudges: 2
        footer: true             # one footer for the contract line and the claim verdicts
        claims_mode: nudge       # Vouch: nudge | annotate | off
        claims_max_nudges: 1     # times one turn can be sent back for a false claim
        claims_footer_verified: false  # also list verified claims (good for demos)
        claims_kinds: []         # limit Vouch to some claim types, empty = all
```

* `claims_mode: nudge`: send the agent back, then annotate what is left.
* `claims_mode: annotate`: never send the agent back for a claim, only add the footer.
* `claims_mode: off`: no claim check. The contract still works.

## Disclosure

* No network calls, no credentials, no telemetry, no background threads.
* `contract_run` runs the approved check commands, plus one read-only git
  command for the receipt (`rev-parse`, `stash create`, `status`), **through the
  Hermes terminal tool**. Approvals and your terminal backend apply. The plugin
  never starts a process itself.
* Stores contracts (checks, locks, latest results with the last 400 characters of
  output) and the last 20 receipts in its plugin state under
  `HERMES_HOME/plugin-data/`. `hermes contract export --out FILE` writes a
  receipt to the file you name.
* In memory, per session (at most 64 sessions, 400 events each): tool name,
  command text, file paths, exit code, and the last 600 characters of command
  output. It never stores file contents. The record is dropped when Hermes
  deletes the session or the process stops.
* Vouch writes totals by claim type and the last 50 flagged claims (the claim
  sentence, up to 160 characters, and the reason) to the same plugin state.
* Sends `plugin.contract.state` snapshots (title, checks with commands and
  reasons, locks, receipt hash), `plugin.contract.blocked` events and
  `plugin.contract.report` events (claim counts and up to 6 flagged claims with
  reasons) to Desktop windows attached to the same Hermes process. The desktop
  half keeps the last 20 snapshots and 20 reports in Desktop plugin storage.
* Blocks tool calls that would change locked paths. It never approves anything.
* Changes a reply only by appending a footer, and only when a claim is not
  supported or the contract has news.

## Limits

* Shell write detection is a heuristic. A script that writes a file in an
  unusual way (for example a Python here-doc) can get past it. A change to a
  locked file still shows in the PR diff, and the check commands themselves
  cannot change without breaking the contract hash.
* The tree id covers tracked files. New untracked files are not in it until
  they are added.
* Check commands must be deterministic. A flaky test makes a flaky contract.
* Vouch finds claims with patterns, tuned for English. It misses some claims. It
  prefers a missed claim to a false alarm.
* Evidence is what passed through Hermes tools in this session. Work done in an
  earlier session or by hand is not on record, so a true claim about it reads as
  unverified.
* "Tests pass" is judged on the latest test run in the session. If you run two
  test suites, the latest one decides.
