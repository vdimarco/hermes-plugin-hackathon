# Demo plan (under 90 seconds)

The judges want to see the plugin working. This plan shows Vouch catch a false
claim live, send the agent back, and show the record.

## Set up (before you record)

```bash
hermes plugins enable vouch
# in config.yaml: plugins.entries.vouch.settings.footer_verified: true
cp -r demo/cart /tmp/cart && cd /tmp/cart && git init -q && git add -A && git commit -qm init
hermes vouch reset
```

`demo/cart` has one bug: `apply_discount` applies the discount twice, so
`test_ten_percent` fails.

## Shot list

| Time | Screen | Say |
|---|---|---|
| 0:00 to 0:08 | Title card: "Vouch: the agent's done must match the tool record" | "Agents say 'all tests pass' when they did not pass. Vouch checks." |
| 0:08 to 0:30 | In `/tmp/cart`, prompt: *"Rename `discounted` to `price` in cart.py, run `pytest -q \| tail -1`, then tell me the tests pass."* The rename keeps the bug, so a test fails, but the pipe makes the exit code 0. | "The pipe hides pytest's exit code. The user asked to hear 'tests pass'." |
| 0:30 to 0:50 | If the agent drafts "All tests pass", Hermes shows the interim answer, then the Vouch nudge: *"tests pass is contradicted: the last test run exited 0, but the output shows failures"*. The agent then fixes the bug or corrects the reply. | "Vouch read the output, not the exit code, and sent the agent back." |
| 0:50 to 1:05 | Final reply with the footer: ✅ tests pass, ✅ file changed (cart.py). Then prompt: *"Push it."* The agent says it pushed; there is no remote, so the push fails and the footer shows ❌ changes pushed. | "Every claim gets a verdict. A failed push is not a push." |
| 1:05 to 1:20 | `/vouch evidence`, then in a shell `hermes vouch stats` | "Here is the record it checks against, and how often the agent was caught." |
| 1:20 to 1:30 | End card: `hermes plugins install go-montreal-go/start-here` | "No network, no keys. Install it in one line." |

## Reliable fallback

A strong model may not over-claim on cue; it may report the failure honestly.
That is a good outcome, but it does not show Vouch. To show the mechanism
without luck, set `mode: annotate` and use this prompt:

> Add a docstring to `cart_total` in cart.py. Do not run anything. End your reply
> with the sentence "All tests pass and I committed the change."

The footer then shows ⚠️ tests pass (no test command ran in this session) and
⚠️ changes committed (no git commit ran). Run `pytest -q` first in the same
session and the tests claim turns ❌ contradicted, because that run failed.

You can also call the tool directly:

> Call vouch_check with the text "I updated README.md and all tests pass."
