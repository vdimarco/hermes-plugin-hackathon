# Contract

**The agent writes the exam before it takes it, and it cannot change the exam.**

Contract is a Hermes Agent plugin. It turns "done" into a short list of shell
checks that you approve before the work starts. After that, the agent cannot
edit the tests, cannot finish while a check fails, and gets a receipt that a
reviewer can check when every check passes.

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

| Hook | What Contract does |
|---|---|
| `pre_tool_call` | Blocks writes to locked paths: `write_file`, `patch` (also V4A bodies), shell writes (`>`, `tee`, `sed -i`, `rm`, `mv`, `cp` into, `git checkout`/`restore`), and file writes in `execute_code`. |
| `subagent_start` | Subagents started from the session inherit the locks, so a delegated helper or a Multiverse attempt cannot edit the tests either. |
| `post_tool_call` | Records every run of a check command with its exit code, also when the agent runs it with the terminal tool directly. A pipe that hides a failure (`pytest \| tail`) still counts as a failure. |
| `pre_verify` | A turn that edited code cannot finish while a check fails, has not run, or passed before the last edit. Bounded by `max_nudges` (default 2) and `agent.max_verify_nudges`. |
| `pre_llm_call` | While a contract is open, adds a one-line reminder of the definition of done. |
| `transform_llm_output` | One status line under replies: proposed, 2/3 pass, or met with the receipt id. |

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

Model tools: `contract_propose`, `contract_status`, `contract_run`.

## Hermes Desktop

The package ships a desktop half (`desktop/plugin.js`). In Hermes Desktop it adds:

* a **Contract** pane with the live checklist of the focused chat (✅ pass, ❌ fail,
  ♻️ stale, ⏳ not run), the locks, and the receipt hash with a copy button;
* a status-bar chip, for example `contract 2/3`;
* a toast when the lock blocks an edit;
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
        footer: true
```

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
* Sends `plugin.contract.state` snapshots (title, checks with commands and
  reasons, locks, receipt hash) and `plugin.contract.blocked` events to Desktop
  windows attached to the same Hermes process. The desktop half keeps the last
  20 snapshots in Desktop plugin storage.
* Blocks tool calls that would change locked paths. It never approves anything.

## Limits

* Shell write detection is a heuristic. A script that writes a file in an
  unusual way (for example a Python here-doc) can get past it. A change to a
  locked file still shows in the PR diff, and the check commands themselves
  cannot change without breaking the contract hash.
* The tree id covers tracked files. New untracked files are not in it until
  they are added.
* Check commands must be deterministic. A flaky test makes a flaky contract.
