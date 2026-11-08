# Changelog

All three plugins (Vouch, Contract, Multiverse) share one version.

## 1.1.1

- Vouch and Contract share one reply footer: Hermes keeps only the first rewrite, so Vouch now carries Contract's status line, and Contract yields while Vouch is enabled.
- Docs: `hermes plugins enable` takes one name.
- `scripts/smoke-hermes.py`: a smoke test inside a real Hermes install (23 checks).
- `contract_status` reports the summary and the receipt hash.

## 1.1.0

- Hermes Desktop halves (`desktop/plugin.js`) for all three plugins: a Vouch status-bar chip, a Contract checklist pane, and a Multiverse live race board. The Python side pushes state with `broadcast_plugin_event`.
- Catalog card images (`assets/catalog/`), and install sections with Hermes Desktop links in the READMEs.
- Catalog entries pin the release commit; tests check that the pin, the version and the images agree.

## 1.0.0

- Vouch: checks "done" claims in the final reply against the tool record (`post_tool_call`, `pre_verify`, `transform_llm_output`).
- Contract: approved checks and locked paths, a finish gate, and hash-chained receipts with `hermes contract verify`.
- Multiverse: races attempts in git worktrees, pins each subagent to its worktree, judges them with the same checks, and adopts the winner.
