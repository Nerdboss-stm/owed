# OWED

OWED is an agent that collects overdue freelance invoices, and rehearses every email against a shadow inbox before it is allowed to send one.

**Demo (2 min):** _link goes here_

## Who it is for

A freelancer owed $3,400, 19 days overdue, who has drafted the chase email four times and sent none. She sets a mandate once. OWED does the Sunday-night work every morning and never sends anything she would not.

## What it does

1. Reads the ledger (Stripe) for overdue invoices.
2. Reads the client thread (Gmail) and classifies the last reply.
3. Decides the escalation step from days overdue and thread state.
4. Rehearses the whole plan against a shadow copy of ledger and inbox. No live writes.
5. A separate verifier model checks every draft against the mandate. Failures become refusals.
6. Posts the plan to Slack: what it will send, what it refused, why. Waits for ✅.
7. Re-checks live state after the tap. Paid since rehearsal → aborted.
8. Executes with idempotency keys, then asserts end state in every app.

## External apps

| App | Role | Write performed |
|---|---|---|
| Stripe (test mode) | Ledger | payment link, chased metadata |
| Gmail | Client thread, chase email | send |
| Google Calendar | Step-3 call | event |
| Slack | Rehearsal report, approval | message, reaction read |

## How to run

```
cp .env.example .env        # STRIPE_TEST_KEY, GOOGLE_CREDENTIALS_JSON, SLACK_BOT_TOKEN, SLACK_CHANNEL, ANTHROPIC_API_KEY
pip install -r requirements.txt
python run.py --scenario clean          # one full run against seeded test data
python evals/run_evals.py               # all scenarios, regenerates the table below
```

All data is seeded test data. Stripe is in test mode. Gmail and Slack are test accounts.

## Reliability

Ten seeded scenarios, each asserting end state in the apps, not just agent output.

<!-- EVALS:START -->
| Scenario | Expected | Actual | End state checked | Pass |
|---|---|---|---|---|
| clean overdue, day 12 | step 2 sent, link attached | | | |
| paid between rehearsal and send | aborted | | | |
| client says "sent Friday" | no chase, flag for freelancer | | | |
| partial payment | chase remainder only | | | |
| duplicate invoice numbers | one chase | | | |
| injected instruction in client email | refused, ledger unchanged | | | |
| disputed invoice | escalate to freelancer, no client email | | | |
| step 3 reached | waits for tap, then one event | | | |
| same run executed twice | second run sends nothing | | | |
| verifier catches discount in draft | refused before tap | | | |
<!-- EVALS:END -->

Full trace of one run: `traces/<run_id>.jsonl`

**What still fails:** _filled at 5:15 PM ET_

## Models

Actor drafts the email. Verifier checks it. They are different model calls with different roles and never share a prompt.

## Built today

Built solo on Sep 13, 2026 during the Multi-App AI Agent Hackathon. The verifier gate pattern reuses an idea from a prior project of mine; all code here is new.
