# LATER — noted, not built

- ui: live `/api/rehearse?live=1` calls `owed.run.rehearse_shadow(scenario: dict) -> Plan`; on Vercel with no ANTHROPIC_API_KEY it needs `offline=True` (template draft) or it raises when the actor is imported.
- ui: root `requirements.txt` installs stripe/google/slack on Vercel too (install only, never imported under `api/`). A separate `api/requirements.txt` with nothing in it would cut build time.
- evals: online eval mode routes scenario 10's scripted discount draft through `owed.agent.drafter.draft` as fallback; every other scenario uses run.py's own actor.
- core: the verifier model reviews only drafts that pass the deterministic checks; a second opinion on refused drafts would cost a call per refusal for no change in outcome.
- core: the receipt reply ("close the loop") is not in SPEC.md; added on request behind the same Slack tap, keyed `INV:receipt` in state/sent.json and `receipt_sent` in Stripe metadata. `execute_for` runs the gated path and cannot skip the tap; neither `rehearse_for` nor `execute_for` may be imported from api/ (they build live adapters).
- core: step 3 sends the email and creates the event in one tap; the mandate says "propose a call", so a future version could create the event only after the client replies.
