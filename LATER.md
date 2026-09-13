# LATER

- evals: `evals/harness.py` assumes `run.run(*, ledger, inbox, calendar, chat, today, run_id, mandate, state_dir, traces_dir, drafter=None, rehearse_only=False)` plus `owed.agent.{gate.classify, verifier.deterministic_checks, drafter.draft, asserter.assert_end_state}`. Reconcile with core at the 2:45 merge by editing the harness only, not tests.
- evals: online eval mode routes scenario 10's scripted discount draft through `owed.agent.drafter.draft` as fallback; every other scenario uses run.py's own actor.
