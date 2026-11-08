# Changelog

The plugins (Contract and Multiverse; Vouch until 1.1.1) share one version.

## 1.2.0

- **Vouch now ships inside Contract.** One install gives the definition-of-done contract and the claim check. Both read one tool record and write one reply footer: Vouch's claim verdicts, then the contract's status line. The claim check works with or without a contract.
- Contract's `pre_verify` runs the contract gate first; when the gate lets the turn end, Vouch checks the draft reply.
- New Contract settings for the claim check: `claims_mode`, `claims_max_nudges`, `claims_footer_verified`, `claims_kinds`. `footer` covers both parts. These replace Vouch's `mode`, `max_nudges`, `footer_verified` and `kinds`.
- Contract's Desktop half adds the Vouch chip (`vouch ✓3 ⚠1 ✗1`) and the "sent back" toast. Events move from `plugin.vouch.report` to `plugin.contract.report`.
- Removed: the separate `vouch` plugin and its catalog entry, and the `footer_request` handoff between the two plugins.
- Multiverse: no change except the version number.

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
