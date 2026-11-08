# Proof of Work for Hermes Agent

Three Hermes Agent plugins that make an agent's "done" something you can check.

| Plugin | One line | Main hooks |
|---|---|---|
| [**Vouch**](plugins/vouch) | Every claim in the final reply ("all tests pass", "pushed to main") is checked against the tool record. False claims send the agent back. | `post_tool_call`, `pre_verify`, `transform_llm_output` |
| [**Contract**](plugins/contract) | You approve the definition of done before work starts. The agent cannot edit the tests, cannot finish while a check fails, and ships a receipt that a reviewer can check. | `pre_tool_call`, `pre_verify`, `pre_llm_call`, `post_tool_call` |
| [**Multiverse**](plugins/multiverse) | Race 2 to 6 attempts (approaches or models) in separate git worktrees, judge them with the same checks, adopt the winner. | `subagent_start`, `pre_tool_call` (path pinning), delegation provider |

Each plugin also ships a Hermes Desktop half (`desktop/plugin.js`): a status-bar
chip for Vouch, a live checklist pane for Contract, and a live race board for
Multiverse. The Python side pushes state with `broadcast_plugin_event`; the
desktop side only listens.

Each plugin works alone. Together they cover a task from start to finish:

```
Contract  ─ what "done" means, approved by you, locked
Multiverse ─ several attempts, judged by those checks, the winner adopted
Vouch     ─ the final report matches what really ran
Contract  ─ receipt for the PR
```

None of them make network calls or hold credentials. Contract and Multiverse
run shell commands only through the Hermes terminal tool, so your approvals and
terminal backend apply.

## Install

```bash
hermes plugins install https://github.com/vdimarco/hermes-plugin-hackathon/tree/main/plugins/vouch
hermes plugins install https://github.com/vdimarco/hermes-plugin-hackathon/tree/main/plugins/contract
hermes plugins install https://github.com/vdimarco/hermes-plugin-hackathon/tree/main/plugins/multiverse
for p in vouch contract multiverse; do hermes plugins enable $p; done   # enable takes one name
```

In Hermes Desktop, paste one of these links into your browser's address bar, or
use **Capabilities → Plugins → Install from Git** with a folder URL above. Hermes
shows what it found and asks before it installs anything.

```text
hermes://plugin/install?repo=https%3A%2F%2Fgithub.com%2Fvdimarco%2Fhermes-plugin-hackathon%2Ftree%2Fmain%2Fplugins%2Fvouch&enable=1
hermes://plugin/install?repo=https%3A%2F%2Fgithub.com%2Fvdimarco%2Fhermes-plugin-hackathon%2Ftree%2Fmain%2Fplugins%2Fcontract&enable=1
hermes://plugin/install?repo=https%3A%2F%2Fgithub.com%2Fvdimarco%2Fhermes-plugin-hackathon%2Ftree%2Fmain%2Fplugins%2Fmultiverse&enable=1
```

Once the plugins are listed in the Hermes catalog, each catalog card has an
**Open in Hermes Desktop** button that installs the reviewed commit.

For local work, link a folder into `~/.hermes/plugins/<name>/` instead.

## Repository layout

```
plugins/<name>/     one Hermes plugin per folder (plugin.yaml + __init__.py + README)
plugins/<name>/desktop/plugin.js   its Hermes Desktop half
catalog/<name>.yaml entries for NousResearch/hermes-agent plugin-catalog/ (pinned sha)
docs/catalog-prs/   the PR description for each catalog entry
scripts/            open-catalog-prs.sh: validate, then open one catalog PR per plugin
tests/              unit tests per plugin, suite rules, and real-git integration tests
demo/cart/          a small project with one bug, for demos
docs/demo.md        the 90-second demo plan
```

## Submit to the Hermes catalog

```bash
gh auth login                        # as the owner of this repo
scripts/open-catalog-prs.sh          # dry run: validates each plugin at the pinned sha, shows the PRs
scripts/open-catalog-prs.sh --open   # pushes to your hermes-agent fork and opens one PR per plugin
```

## Test

```bash
python -m unittest discover -s tests -t . -v     # needs PyYAML, git and bash (and node for the desktop tests)
node --import ./tests/desktop/register.mjs --test tests/desktop/test.mjs
for p in plugins/*/; do hermes plugins validate "$p" --install-deps; done
```

The integration tests run the plugins' real git commands in a temporary
repository: worktrees, pinned writes, judging, `git apply`, cleanup, and the
receipt's tree id against a real commit.

## License

MIT
