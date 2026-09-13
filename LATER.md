# LATER — noted, not built

- ui: `.gitignore` ignores `state/`, but DEPLOY.md says commit `state/rehearsals/*.json`. Vercel CLI uploads follow `.gitignore`, so precomputed plans must be un-ignored (`!state/rehearsals/`) before the 4:45 merge or `api/rehearse.py` keeps serving `ui/fixtures/`.
- ui: live `/api/rehearse?live=1` expects core to expose `owed.run.rehearse_shadow(scenario: dict) -> Plan`. Until that exists the endpoint reports `live_error` and serves the precomputed plan.
- ui: `api/last_run.py` expects run.py to PUT `last_run/<run_id>.json` (Plan, plus `end_state` list) and `last_run/<run_id>.jsonl` (trace) to Blob. It picks the newest of each by `uploadedAt`.
- ui: root `requirements.txt` installs stripe/google/slack on Vercel too (install only, never imported under `api/`). A separate `api/requirements.txt` with nothing in it would cut build time.
