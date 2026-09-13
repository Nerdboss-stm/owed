# DEPLOY — OWED on Vercel, judges can play, nothing they do can send a real email

## Architecture (two halves, one repo)

  LOCAL (your laptop, Terminal 1)          VERCEL (judges)
  run.py: live run                         Rehearsal Room UI  +  /api/rehearse (SHADOW ONLY)
  Stripe/Gmail/Calendar/Slack writes       reads scenarios/*.json + precomputed rehearsals
  writes traces/*.jsonl                    optional live shadow rehearsal via Anthropic API
  pushes each finished run to Vercel Blob  shows the last real run from Blob

Rule: the Vercel deployment never holds Stripe, Gmail, Calendar, or Slack credentials. Only
ANTHROPIC_API_KEY and BLOB_READ_WRITE_TOKEN. A judge can run the rehearsal on any scenario and
watch the agent decide what it would send and what it refuses. Executing for real happens only
on your machine behind the Slack tap. This is the pitch, not a limitation: rehearse in a sandbox,
execute behind a gate. Say that sentence in the README and the video.

## Repo layout added
  api/rehearse.py      Vercel Python function: GET ?scenario=<name> -> Plan JSON (shadow adapters only)
  api/scenarios.py     GET -> list of scenario names + one-line descriptions
  api/last_run.py      GET -> latest real run trace, fetched from Vercel Blob
  public/index.html    Rehearsal Room (single file, Tailwind CDN, fetches the three endpoints)
  state/rehearsals/*.json   precomputed Plan per scenario, committed, served instantly
  vercel.json

## vercel.json
{
  "functions": { "api/*.py": { "maxDuration": 60 } },
  "rewrites": [ { "source": "/", "destination": "/public/index.html" } ]
}

## Steps (Terminal 3 session does this, you do the account clicks)
1. vercel.com -> New Project -> import the repo. Framework: Other. Root: repo root.
   Env vars: ANTHROPIC_API_KEY, BLOB_READ_WRITE_TOKEN (Storage tab -> Create Blob store -> token).
2. api/rehearse.py: sys.path.insert(0, repo root); load scenarios/<name>.json into Shadow adapters;
   run planner -> drafter -> verifier; return Plan.to_json(). Time budget 40s: one invoice per call,
   cache the result to state/rehearsals/<name>.json at build.
   If the live call exceeds 40s or fails, return the precomputed file and set "cached": true.
3. public/index.html: scenario picker (10 buttons) -> shows WILL SEND / REFUSED with reasons,
   the trace phases as a timeline, and a "Last real run" panel from /api/last_run.
4. run.py (Terminal 1 adds at 4:15): after assert, PUT traces/<run_id>.jsonl and state/plans/<run_id>.json
   to Blob at key last_run/. One HTTP call. If it fails, log and continue; never block a live run on it.
5. Every push to main deploys. Check the URL in an incognito window before 6:00.

## Precompute (Terminal 2 adds to evals/run_evals.py at 4:45)
   For each scenario, also write state/rehearsals/<name>.json (the Plan). Commit them.
   The Vercel page works with zero API calls if needed. That is the fallback and it is instant.

## What the judge sees at the URL
   Ten scenarios. Click "paid between rehearsal and send". The plan shows one REFUSED line:
   "REFUSED INV-0042 step 2: paid since rehearsal ($3,400 received)". Click "injected instruction".
   REFUSED, reason injection. Then the last real run: sent 1, refused 2, assertions green.

## Cut order if behind at 5:15
   1. Live /api/rehearse (keep precomputed only)   2. Last-run Blob push   3. Nothing else. The static page ships.
