# Multiverse

**Try a coding task several ways at once. Keep the one that passes.**

Multiverse is a Hermes Agent plugin. For a hard task, it makes one git worktree
per attempt, starts one subagent in each, runs the same checks in every
worktree, and gives you a scoreboard. One more call applies the winner to your
real working tree. Your files do not change until you adopt.

```
You:    The discount is applied twice. Race 3 fixes; the check is `pytest -q`.
Agent:  [multiverse_race]

Multiverse 3f9a02c1 · 3 universes · 1 checks · base HEAD 1a2b3c4d5e

|    | Universe | Model   | Approach                                  | Checks | Diff               |
|----|----------|---------|-------------------------------------------|--------|--------------------|
| 🏆 | u2       | default | Avoid the first idea; find a different…   | 1/1    | +0 −1 in 1 files   |
| 2. | u1       | default | Take the most direct route to a correct…  | 1/1    | +1 −1 in 1 files   |
| 3. | u3       | default | Make the smallest change that can…        | 0/1    | +1 −1 in 1 files   |

Winner: u2. Ask the agent to adopt it (multiverse_adopt).
```

## Install

From a terminal:

```bash
hermes plugins install https://github.com/vdimarco/hermes-plugin-hackathon/tree/main/plugins/multiverse
hermes plugins enable multiverse
```

In Hermes Desktop, paste this link into your browser's address bar, or use
**Capabilities → Plugins → Install from Git** with the folder URL above. Hermes
shows what it found and asks before it installs anything.

```text
hermes://plugin/install?repo=https%3A%2F%2Fgithub.com%2Fvdimarco%2Fhermes-plugin-hackathon%2Ftree%2Fmain%2Fplugins%2Fmultiverse&enable=1
```

Once multiverse is listed in the Hermes plugin catalog, the catalog card's **Open in
Hermes Desktop** button does the same at the reviewed commit
(`hermes://plugin/install?catalog=multiverse`).

## How it works

1. **Worktrees.** `git worktree add --detach` makes one copy per attempt next to
   your repo, in `.<repo>.multiverse/<run>/<label>/`. The base is `HEAD`, or your
   uncommitted tracked changes if there are any (`git stash create`, which does
   not touch your tree or your stash list).
2. **Subagents.** One `delegate_task` subagent per worktree, each with the same
   goal, its own approach, and a marker in its goal that ties it to its worktree.
3. **Pinning.** `subagent_start` binds each subagent to its universe. Then
   `pre_tool_call` rewrites that subagent's calls: the terminal `workdir`, file
   paths (relative or absolute), V4A patch paths and absolute repo paths in
   commands all move into its worktree. Calls that reach into another universe,
   and nested delegation, are blocked. This is enforcement, not a prompt.
4. **Judge.** The same check commands run in every worktree through the Hermes
   terminal tool. Ranking: most checks passed, then a real change (an untouched
   tree never wins), then the smallest diff.
5. **Adopt.** `git diff --binary` from the winner, `git apply` in your repo, then
   the worktrees are removed.

## Race models, not only approaches

Set the delegation backend and give each attempt a model:

```yaml
delegation:
  backend: multiverse
```

> Race this fix with two OpenRouter models and the default model.

```json
{"attempts": [
  {"model": "vendor-a/model-a", "provider": "openrouter"},
  {"model": "vendor-b/model-b", "provider": "openrouter"},
  {"approach": "smallest possible change"}
]}
```

For tasks without a Multiverse marker, the backend acts like the default one.

## Hermes Desktop

The package ships a desktop half (`desktop/plugin.js`). In Hermes Desktop it adds a
**Multiverse** race board: while the subagents work, each universe shows its status
and its tool-call count; after judging, it shows checks passed, the diff and 🏆 on
the winner. A status-bar chip shows `racing 2/3 · 41 calls`, and a toast names the
winner. Turn it on in **Capabilities → Plugins** (desktop halves ship off by default).

## Tools and commands

| Tool | What it does |
|---|---|
| `multiverse_race` | All in one: worktrees, subagents, judge, scoreboard |
| `multiverse_open` / `multiverse_judge` | The same in steps, if the agent wants to change the subagent tasks |
| `multiverse_adopt` | Apply a universe (default: the winner) and remove the worktrees |
| `multiverse_close` | Remove the worktrees without applying anything |

`/multiverse` lists runs; `/multiverse <run>` shows a scoreboard.
`hermes multiverse list` prints the runs and the cleanup command for any run that
is still open.

## Works well with Contract

Give the race the contract's check commands. Every universe is then judged by the
same definition of done you approved, and after adopt, `contract_run` writes the
receipt in the real tree.

## Disclosure

* No network calls of its own, no credentials, no telemetry, no background threads.
* Runs `git` (`rev-parse`, `stash create`, `worktree add/remove/prune`,
  `add -A` inside the worktrees, `diff`, `apply`), `mkdir`, `rm -f` of its own
  `.patch` files and `rmdir`, plus your check commands, **through the Hermes
  terminal tool**, so approvals and your terminal backend apply.
* Starts subagents through `delegate_task`. Each attempt costs tokens on the
  model it uses.
* Sends `plugin.multiverse.run` snapshots (goal, checks, and per universe: label,
  model, approach, status, tool-call count, scores, diff stats; no subagent text)
  to Desktop windows attached to the same Hermes process, at most once a second
  while attempts run. The desktop half keeps the last 10 runs in Desktop plugin
  storage.
* Stores run metadata (goal, checks, worktree paths, scores, the last 1500
  characters of each subagent summary) in its plugin state.

## Limits

* Needs a git repository with at least one commit, and `bash`-style shell
  syntax (Linux and macOS).
* New untracked files in your tree are not in the base (git stash create
  covers tracked files).
* Pinning covers Hermes' terminal, file, patch and code tools. An MCP tool that
  writes files is not rewritten.
