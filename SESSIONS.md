# SESSIONS — three Claude Code sessions in parallel, disjoint files, merge at checkpoints

Shared venv lives OUTSIDE the repo so every worktree can run tests:
  python3 -m venv ~/.venvs/owed && source ~/.venvs/owed/bin/activate && pip install -r requirements.txt
Activate it in every terminal before `claude`.

Order of operations (do not skip):
  1. main: commit SPEC.md PLAN.md CLAUDE.md README.md SESSIONS.md owed/contract.py .claude/ .worktreeinclude
  2. main: run the 20-min connector check by hand (PLAN 1:05). Commit .env locally only (gitignored).
  3. open three terminals, activate venv in each, then:

## Terminal 1 — CORE (stays on main, owns adapters/, agent/, run.py)
  claude
  First message:
    Read CLAUDE.md, SPEC.md, owed/contract.py. Implement PLAN.md tasks 1:25 through 4:15 in order,
    one task at a time, run the task's check before starting the next. Live adapters wrap the calls
    already proven in the connector check. Shadow adapters never import network libs. Stop and show
    me output at each check.

## Terminal 2 — EVALS (worktree, owns scenarios/, evals/, tests/)
  claude --worktree evals
  First message:
    Read CLAUDE.md, SPEC.md, owed/contract.py. Build scenarios/*.json for the ten SPEC table rows,
    in-memory stub adapters that implement the contract ABCs and count writes, tests/test_ac1..ac7
    against the stubs, and evals/run_evals.py that writes the markdown table between the
    EVALS markers in README.md. Do not touch adapters/ or agent/. Where you need the agent, import
    from owed.agent and if it is missing, write the test against the interface and mark it xfail
    until merge.

## Terminal 3 — UI + VERCEL (worktree, owns api/, public/, vercel.json)
  claude --worktree ui
  First message:
    Read CLAUDE.md, SPEC.md, DEPLOY.md, owed/contract.py. Build exactly what DEPLOY.md describes:
    api/rehearse.py, api/scenarios.py, api/last_run.py, public/index.html "Rehearsal Room",
    vercel.json. Shadow adapters only in api/. Never import stripe, googleapiclient, or slack_sdk
    inside api/. Use ui/fixtures/ Plan JSON until scenarios exist. Layout: scenario picker top;
    center WILL SEND and REFUSED lists with reasons; right end-state assertions per app green/red;
    bottom "Last real run" from Blob. Dark, dense, readable at 1080p in a screen recording.
    Deploy with `vercel --prod` and give me the URL.

## Merge checkpoints (Terminal 1 does the merges)
  2:45  git merge evals   (stubs + tests land; core runs tests)
  4:15  git merge ui      (Terminal 1 then adds the Blob push to run.py, DEPLOY.md step 4)
  4:45  Terminal 2 precomputes state/rehearsals/*.json, merge, push -> Vercel redeploys
  5:15  final merge, cut review, README (add the Vercel URL under the demo link)

## Claude Code desktop app (Code tab) — exact clicks

Setup once (5 min):
  1. Terminal: unzip kit into repo, `git init`, commit, push to a PUBLIC GitHub repo.
  2. Terminal: python3 -m venv ~/.venvs/owed && source ~/.venvs/owed/bin/activate && pip install -r requirements.txt
  3. Terminal: connector check (PLAN 1:05). Write .env. It is gitignored; .worktreeinclude copies it into every session.
  4. Claude Desktop -> Code tab -> + New session. Environment: Local. Project folder: the repo.
     Model: the strongest available. Permission mode: Accept edits (settings.json already defaults to it).
     Optional: Settings -> Claude Code -> "Allow bypass permissions mode" if you want zero prompts.

Three sessions:
  Session 1 CORE: + New session, project = repo, do NOT select the worktree option (it works on main).
     Paste the CORE first message. Rename the session "core" (click the title in the toolbar).
  Session 2 EVALS: Cmd+N, same repo, SELECT the "worktree" option next to the branch name. Paste the EVALS
     first message. Rename "evals".
  Session 3 UI: Cmd+N, same repo, SELECT worktree. Paste the UI first message. Rename "ui".
  settings.json sets worktree.baseRef = "head", so each worktree branches from your current commit, not origin.
  Commit contract.py BEFORE opening sessions 2 and 3 or they will not see it.

While building:
  - Ctrl+` opens a terminal INSIDE the session's own directory (worktree-aware). Use it to run pytest there.
  - Cmd-click a second session in the sidebar to see two side by side.
  - Cmd+; opens a side chat that reads the session context without derailing it. Use it for "why did this fail".
  - Session 3: open the Browser pane (Cmd+Shift+B). Claude will start the FastAPI dev server and screenshot the
    Rehearsal Room itself. Add .claude/launch.json if it picks the wrong command.
  - Cross-session: in session 1 type "tell the evals session that contract.py changed" and it delivers it.
  - Usage ring next to the model picker shows plan usage. If it is red by 3 PM, archive session 3.

Merges (2:45, 4:15, 5:15) happen in SESSION 1 only, because worktree sessions are blocked from touching main:
  In session 2 and 3: "commit everything and tell me the branch name" (it is worktree-<name> or your prefix).
  In session 1: "git merge <evals-branch> then run pytest; resolve conflicts in favor of the contract".
  Then in session 1: "git push" so Vercel redeploys.

End: hover a finished session in the sidebar and click the archive icon to remove its worktree.
