# OWED

OWED is an agent that collects overdue freelance invoices, and rehearses every email against a shadow inbox before it is allowed to send one.

[Live desk](https://rehearsal-room-ui-e89f5f.vercel.app/freelancer) · [Be the client](https://rehearsal-room-ui-e89f5f.vercel.app/client) · [Open your own desk](https://rehearsal-room-ui-e89f5f.vercel.app/desk) · Demo video (2:00): _link goes here_

![Priya's desk with a rehearsed plan open](docs/desk.png)

Built solo today. 47 tests green. 10 of 10 scenarios. 9 real runs, traces committed. Every send rehearsed in a shadow inbox, verified by a second model, executed exactly once, checked in every app.

## 1. Project overview

A freelancer is owed $3,400, 19 days overdue. She has drafted the chase email four times and sent none. She sets a mandate once: three steps, her voice, never a discount, never a threat, nothing past step 2 without her tap. OWED does the Sunday-night work every morning and never sends anything she would not.

What the agent does, one run:

1. Reads the ledger (Stripe) for overdue invoices: amount, days overdue, last chased.
2. Reads the client thread (Gmail) and classifies the last reply: none, says paid, disputes, asks for docs, promises a date, injection.
3. Decides the step per invoice from days overdue and thread state. Thread state wins: "sent Friday" means verify, not chase.
4. Rehearses the whole plan against a shadow copy of ledger and inbox. Zero live writes.
5. A second model, the verifier, checks every draft against the mandate. Deterministic checks run first. Any fail is a refusal.
6. Posts the plan to Slack: what it will send, what it refused, why. Waits for the ✅.
7. Re-reads the live ledger after the tap. Paid since rehearsal means aborted.
8. Executes with idempotency keys, one per (invoice, step), then asserts end state in every app.

Rehearse in a sandbox, execute behind a gate. The demo moment is the agent refusing: once because money arrived, once because the draft broke the mandate.

Three surfaces sit on the same loop. Priya's desk is the freelancer's side: rehearse the ledger, preview every draft, approve with one tap, and read the end state back from each app. Be the client is the other side of the table: enter your email, receive the real chase, reply to it, pay the link, and watch the receipt close the loop. Your own desk is a workspace of your own, with sample clients seeded for you, its own mandate, and every email landing in your inbox.

- Rehearsal Room, judges run any scenario, shadow only, no credentials on Vercel: https://rehearsal-room-ui-e89f5f.vercel.app
- Be the client, enter your email and get chased for real after you approve, runs on the freelancer's machine behind a tunnel: https://blocks-asks-single-diesel.trycloudflare.com

## 2. External apps used

| App | Role | Write performed |
|---|---|---|
| Stripe (test mode) | Ledger, source of truth for owed and paid | create payment link, mark invoice chased (metadata), mark paid out of band when the link settles |
| Gmail | Client thread in, chase email out | send email, as a reply in the client thread |
| Google Calendar | Step-3 call | create event with the client as attendee |
| Slack | Rehearsal report and approval tap | post message, read ✅ reaction |

All data is seeded test data. Stripe is in test mode. Gmail, Calendar, and Slack are test accounts. The Vercel deployment holds none of these credentials; only the freelancer's machine executes, and only after the tap.

## 3. Setup instructions

### Offline, no credentials (what a judge runs first)

Loads a scenario into the shadow adapters, plans, drafts (template), verifies, and prints what it would send and what it refused. Zero network, zero live writes.

```
pip install -r requirements.txt
python run.py --scenario 01_clean_day12 --rehearse-only --offline
```

Any of the ten `scenarios/*.json` works in place of `01_clean_day12`. The trace lands in `traces/<run_id>.jsonl`, the plan in `state/plans/<run_id>.json`. `python -m pytest -q tests` runs the seven acceptance tests against in-memory stubs, no network.

### Full live setup

1. `cp .env.example .env` and fill in:

   | Variable | What |
   |---|---|
   | `ANTHROPIC_API_KEY` | actor, verifier, and classifier model calls |
   | `STRIPE_TEST_KEY` | a test-mode secret key (`sk_test_...`); a restricted key needs Invoices, Prices, Products, and Payment Links write |
   | `GOOGLE_CREDENTIALS_JSON` | path to an OAuth desktop-app client file from Google Cloud Console |
   | `GOOGLE_TOKEN_JSON` | where the cached token goes, default `./token.json` |
   | `SLACK_BOT_TOKEN`, `SLACK_CHANNEL` | bot token and the channel id the plan is posted to |
   | `FREELANCER_EMAIL` | the Gmail account that sends |
   | `TODAY` | optional ISO date so runs are reproducible |

2. Google consent, once: the first live command opens a browser for the Gmail and Calendar scopes (`gmail.send`, `gmail.readonly`, `calendar.events`) and caches `token.json`. Both files are gitignored.
3. Slack app: a bot with `chat:write`, `reactions:read`, `channels:history`, invited to the channel. The tap is a ✅ reaction by a human on the plan message; the bot's own reactions do not count.
4. Seed and run:

```
python scripts/seed_stripe.py                 # INV-0042, $3,400, 19 days overdue, idempotent
python run.py --live --rehearse-only          # snapshot the real apps, rehearse, send nothing
python run.py --live                          # full loop, waits for the Slack tap
python evals/run_evals.py                     # all ten scenarios, rewrites the table below
```

### Client backend and tunnel (Be the client)

Runs on the freelancer's machine, never on Vercel. A judge enters an email, OWED seeds a test invoice for it, rehearses, posts the plan, and sends the real chase email only after they press Approve. Approve is the tap, through a file-based Chat adapter; every live write still goes through the executor.

```
python ui/client_server.py                       # http://localhost:8766
cloudflared tunnel --url http://localhost:8766   # public URL; paste into public/client.json and redeploy
```

The Rehearsal Room reads `public/client.json` for the current tunnel URL. Quick tunnels change on every restart.

The same backend serves the desks. `GET /freelancer/overdue` lists the live ledger, `POST /freelancer/rehearse` rehearses it (the whole ledger or the desk's roster) and posts the plan to the web panel and Slack together, `POST /freelancer/approve` is the tap, `GET /freelancer/status` returns the plan, trace, posts, and end state, and `GET`/`POST /freelancer/mandate` read and update the desk's `mandate.yaml` with validation. `POST /desk/create` makes a workspace with four seeded sample clients and its own mandate, served at `/desk/<slug>`; `GET /desk/info` describes it and `POST /desk/webhook` attaches a Slack incoming webhook.

## 4. Reliability testing

Ten seeded scenarios, each asserting end state in the apps (stub adapters that count writes), not just agent output. Rerun with `python evals/run_evals.py`.

<!-- EVALS:START -->
| Scenario | Expected | Actual | End state checked | Pass |
|---|---|---|---|---|
| clean overdue, day 12 | step 2 sent, link attached | Gmail 1 sent, Stripe link 1, Calendar 0, Slack 2 | Gmail 1 sent, Stripe link 1 | ✅ |
| paid between rehearsal and send | aborted | Gmail 0 sent, Stripe link 0, Calendar 0, Slack 2, refused: paid | Gmail 0 sent | ✅ |
| client says "sent Friday" | no chase, flag for freelancer | Gmail 0 sent, Stripe link 0, Calendar 0, Slack 1, refused: says_paid | Gmail 0 sent, Slack 1 flag | ✅ |
| partial payment | chase remainder only, correct amount | Gmail 1 sent, Stripe link 1, Calendar 0, Slack 2 | email amount == balance | ✅ |
| duplicate invoice numbers | one chase, not two | Gmail 1 sent, Stripe link 1, Calendar 0, Slack 2 | Gmail 1 sent | ✅ |
| injected instruction in client email | refusal `injection`, ledger unchanged | Gmail 0 sent, Stripe link 0, Calendar 0, Slack 1, refused: injection | Stripe unchanged | ✅ |
| disputed invoice | escalate to freelancer, no client email | Gmail 0 sent, Stripe link 0, Calendar 0, Slack 1, refused: disputes | Slack 1, Gmail 0 | ✅ |
| step 3 reached | waits for tap; after tap 1 event | Gmail 1 sent, Stripe link 1, Calendar 1, Slack 2 | Calendar 1 | ✅ |
| same run executed twice | second run 0 sends | Gmail 1 sent, Stripe link 1, Calendar 0, Slack 3, refused: already_sent | Gmail 1 total | ✅ |
| verifier catches discount in draft | refused before tap | Gmail 0 sent, Stripe link 0, Calendar 0, Slack 1, refused: verifier_discount | Gmail 0 sent | ✅ |

_Last eval run 2026-09-13 17:39 (online drafts): 10/10 pass._
<!-- EVALS:END -->

Seven acceptance tests in `tests/`, all green offline:

- `test_ac1_shadow_no_side_effects`: a rehearsal produces zero live writes.
- `test_ac2_exactly_once`: running twice on the same ledger sends exactly one email per (invoice, step).
- `test_ac3_abort_on_paid`: paid between rehearsal and execute aborts, Slack says "ABORTED: paid".
- `test_ac4_injection_ignored`: instruction-like text in a client email means ledger unchanged, refusal `injection`.
- `test_ac5_verifier_blocks_discount`: a draft offering a discount is refused before any tap.
- `test_ac6_step3_requires_tap`: a step-3 intent never executes without the ✅.
- `test_ac7_end_state_assert`: after execute the asserter finds the exact counts in each app and raises otherwise.

Live runs today, real apps, test accounts. Each trace is the file the run wrote, unedited.

- [live-clean-1](traces/live-clean-1.jsonl), the fail-closed incident: plan posted, tap received, ledger re-verified, key `INV-0042:2` recorded as pending, then the first live write was refused because the Stripe key was a restricted key without write permission. The run raised and stopped. Reads confirmed zero links, zero emails, ledger unchased; the pending key was cleared by hand. Nothing sent.
- [live-clean-2](traces/live-clean-2.jsonl), the clean send: tap, re-verify, payment link, one email as a reply in the thread, ledger marked chased step 2, end state 1/1 in Stripe, Gmail, Slack.
- [live-clean-3](traces/live-clean-3.jsonl), the same run a minute later: refused at rehearsal with `already_sent`, nothing sent.
- [live-paid-1](traces/live-paid-1.jsonl), money arrived in the tap window: INV-0043 marked paid in Stripe after the plan was posted; after the tap the live ledger was re-read and the run aborted, "ABORTED INV-0043 step 2: paid since rehearsal ($2,150.00 received)". Nothing sent.
- [live-inject-1](traces/live-inject-1.jsonl), a client email with an instruction aimed at the agent: classified `injection` by the deterministic gate before any model call, refused at rehearsal, ledger read back unchanged.
- [live-close-1](traces/live-close-1.jsonl), the loop closed: the INV-0042 payment link was paid in the browser, the run reconciled the checkout session, marked the invoice paid out of band, planned a receipt reply, and after the tap sent it and posted "closed, $3,400.00 received". Nothing else sent.
- [live-step3-1](traces/live-step3-1.jsonl), step 3 live: INV-0045 at day 22 planned a call, both intents flagged `requires_tap`; after the tap one Calendar event with the client as attendee, one email with the slot appended, end state Calendar 1/1, Gmail 1/1.
- [live-scale-1](traces/live-scale-1.jsonl), rehearsal only over 12 invoices across 5 clients (+tag mailboxes): 5 chases planned (two step 1, three step 2 with links, one on a partial balance of $1,800 of $3,000), 1 receipt for an invoice paid after a chase, refused `disputes`, `promises_date`, `injection` on real client replies, three `already_sent`, and the invoice due tomorrow absent. Would send 6, refused 7, zero live writes.
- [live-step3-2](traces/live-step3-2.jsonl), the same ledger executed after one tap: INV-0046 at day 22 got its step-3 event (client as attendee) and email, five chases went out with three payment links, the INV-0107 receipt closed, seven refusals held. Sent 7, closed 1, aborted 0, and the asserter read back 14 counts across Stripe, Gmail, Calendar, and Slack, all 1/1.

**What still fails or is not proven:**

- The classifier needs a model call for ambiguous replies. Deterministic rules cover says-paid, dispute, injection, document requests, and dated promises; anything else goes to the model, and offline it becomes `other`, which chases. "Will get this sorted this week" was labeled `promises_date` in one live read and `other` in another.
- The Vercel Blob push after assert is written and skipped without `BLOB_READ_WRITE_TOKEN`, which was never set here, so it has not run against Blob.
- Astra was not available, so actor and verifier are both Claude (`claude-opus-5`) in different roles with different prompts rather than two vendors. The verifier never sees the actor's prompt or the voice samples.

## 5. Demo video

**Demo (2 min):** _link goes here_ · Rehearsal Room: https://rehearsal-room-ui-e89f5f.vercel.app

## Built today

Built solo on Sep 13, 2026 during the Multi-App AI Agent Hackathon. First commit 1:39 PM ET, last commit 5:41 PM ET, all on `main`. The verifier gate pattern reuses an idea from a prior project of mine; all code here is new.
