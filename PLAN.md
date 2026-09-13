# OWED — PLAN (ET clock, every task ≤ 30 min, each ends with a check)

Hard stops: 5:15 cut review. 6:00 record no matter what. 6:30 submit.

## 12:50–1:05  Repo + README skeleton
- Create public repo `owed`. Add SPEC.md, PLAN.md, CLAUDE.md, README.md skeleton.
- README line 1 is the one-liner. Add the empty reliability table and a "Demo:" placeholder link.
- Check: repo public, README renders, table renders.

## 1:05–1:25  Connector checks (20 min hard cap)
- Gmail + Calendar: one OAuth desktop flow, scopes gmail.send, gmail.readonly, calendar.events. Send one test email to yourself. Create one test event.
- Slack: bot token with chat:write, reactions:read, channels:history. Post one message, add ✅, read it back.
- Stripe test mode: create one customer + one invoice via API, read it back.
- Check: 4 green, or Stripe red → switch to `ledger.json` + MANDATE gate as app 3. Decide at 1:25, not later.

## 1:25–1:50  Adapters, interfaces + Live
- `adapters/ledger.py`, `inbox.py`, `calendar.py`, `chat.py`: abstract interface + Live class each.
- Live classes wrap the calls proven at 1:05–1:25. Each Live write increments a global counter (for AC1).
- Check: `python -c` smoke: list invoices, read one thread, post one Slack line.

## 1:50–2:15  Shadow adapters
- Shadow classes take a snapshot dict and implement the same interface. Writes append to `intents[]`, never touch the network.
- `snapshot()` helper copies live state into shadow.
- Check: AC1 passes (rehearsal → live write counter == 0).

## 2:15–2:45  Planner + thread classifier
- `planner.py`: days_overdue, last_chased, thread_state → step or refusal. Pure function, unit-tested.
- Thread classifier: one model call → {none, says_paid, disputes, asks_docs, promises_date, other}; deterministic keyword pre-check for "paid", "dispute".
- Check: 6 unit cases pass including says_paid → no chase, disputes → escalate.

## 2:45–3:15  Drafter (actor) + payment link
- `drafter.py`: actor model writes the email from mandate + voice samples + invoice facts. Output JSON {subject, body, amount, step}.
- Stripe payment link created only inside executor, never in drafter.
- Check: 3 drafts read in her voice, amount matches ledger.

## 3:15–3:45  Verifier + gate
- Deterministic checks: amount == balance, no discount words, no new deadline, no threats list.
- Verifier model (different model or role) returns {pass, reasons}. Any fail → Refusal with reason.
- `gate.py`: injection check on client thread (instruction-like text → refusal `injection`).
- Check: AC4, AC5 pass.

## 3:45–4:15  Rehearsal → Slack → tap → re-verify → execute → assert
- `run.py`: snapshot → shadow run → plan → post to Slack → poll ✅ → re-read live ledger → executor with idempotency key (invoice_id, step) stored in `state/sent.json` → asserter counts.
- Check: one full live run end to end on the clean scenario. AC2, AC3, AC6, AC7 pass.

## 4:15–4:45  Scenarios
- Ten `scenarios/*.json` from SPEC table. Each seeds ledger + inbox snapshot; live adapters replaced by in-memory Live-like stubs that count writes.
- Check: each scenario loads and runs without error.

## 4:45–5:15  Eval run + table
- `evals/run_evals.py` runs all ten, writes the markdown table into README between markers, saves traces.
- Check: real numbers in README. Failures listed with cause. Do not fix failures past 5:15.

## 5:15  CUT REVIEW (5 min)
- Apply SPEC cut list top-down until the remaining work fits before 5:50.

## 5:15–5:50  README
- Sections in order: one-liner, who it is for (the person, the $3,400), what it does (loop in 8 lines), external apps table, how to run (env vars, test mode note, one command), reliability (table + link to one full trace + "what still fails"), demo link, models used (actor vs verifier).
- Check: a stranger can run `python run.py --scenario clean` in under 5 minutes reading only the README.

## 5:50–6:00  Stage the demo
- Terminal + Slack + Gmail visible. Seed the clean scenario and the paid-between scenario. No script beyond the four timestamps in SPEC.

## 6:00–6:25  Record (real runs, cuts allowed)
- 0:00–0:15 the person and the number. 0:15–1:10 one run to send. 1:10–1:45 the two refusals from the trace. 1:45–2:00 table, repo, one sentence of what still fails.
- Upload unlisted. Paste link into README. Commit.

## 6:25–6:35  Submit
- Form: email + repo link. One submission. Confirm repo is public in an incognito window.
