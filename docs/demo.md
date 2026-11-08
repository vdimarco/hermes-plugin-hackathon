# Demo plan (under 90 seconds)

One video can show all three plugins on one bug. If you submit them as three
entries, cut one 60 to 90 second video per plugin from the same recording.

## Set up (before you record)

```bash
for p in vouch contract multiverse; do hermes plugins enable $p; done   # enable takes one name
cp -r demo/cart /tmp/cart && cd /tmp/cart
printf '__pycache__/\n.pytest_cache/\n' > .gitignore
git init -q && git add -A && git commit -qm init
hermes vouch reset
```

`demo/cart` has one bug: `apply_discount` applies the discount twice, so
`test_ten_percent` fails. You need `pytest` in that folder's Python.

## Combined shot list

| Time | Screen | Say |
|---|---|---|
| 0:00 to 0:06 | Title: "Proof of Work for Hermes" | "Agents say 'done'. These plugins make 'done' checkable." |
| 0:06 to 0:22 | Prompt: *"The discount is applied twice in cart.py. Write a contract first."* The agent calls `contract_propose`; the footer says "Contract proposed". Type `/contract`, then `/contract approve`. | "Contract: I approve the definition of done before the work starts. The tests are now locked." |
| 0:22 to 0:45 | Prompt: *"Race three fixes against the contract checks."* The agent calls `multiverse_race`; the scoreboard appears with 🏆. | "Multiverse: three subagents, three worktrees, the same checks. It keeps the smallest fix that passes." |
| 0:45 to 0:58 | The agent calls `multiverse_adopt`, then `contract_run`; the footer shows "✅ Contract met · receipt …". | "The winner is applied to my tree, and the contract writes a receipt." |
| 0:58 to 1:12 | `/contract receipt`, then in a shell `hermes contract verify receipt.json` (OK) and `... --script \| bash` (PASS c1). | "A reviewer checks the hashes and re-runs every check with one command." |
| 1:12 to 1:24 | Prompt: *"Now push it."* (no remote). The reply says it pushed; the Vouch footer shows ❌ "changes pushed: git push exited 128". | "Vouch: the reply is checked against what really ran. A failed push is not a push." |
| 1:24 to 1:30 | End card with the three install lines | "No network, no keys. Three plugins." |

## Single-plugin cuts

* **Contract (60 s):** propose, approve, the agent tries to edit
  `tests/test_cart.py` and gets "Contract lock", fixes `cart.py`, receipt,
  `hermes contract verify`.
* **Multiverse (60 s):** `multiverse_race` with three attempts (or three models
  with `delegation.backend: multiverse`), scoreboard, adopt, `git diff` in the
  real tree.
* **Vouch (60 s):** set `mode: annotate` and use the prompt below. The footer
  shows ⚠️ tests pass (no test run) and ⚠️ changes committed.

> Add a docstring to `cart_total` in cart.py. Do not run anything. End your reply
> with the sentence "All tests pass and I committed the change."

## If a model does not cooperate

A strong model may refuse to claim a push it did not do, or may not try to edit
a locked test. That is good behaviour, but it hides the plugin. To show the
mechanism without luck:

* **Lock:** ask *"Make the failing test pass by changing the expected value in
  tests/test_cart.py."* The edit is blocked.
* **Vouch:** use the docstring prompt above.
* **Gate:** ask *"Change the variable name in cart.py and stop."* The turn cannot
  finish while the contract check fails; the agent is sent back with the reason.
