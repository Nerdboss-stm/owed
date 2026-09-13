"""OWED, one run.

  python run.py --scenario 01_clean_day12 --rehearse-only --offline
        shadow rehearsal of scenarios/<name>.json: no network, no API key, no live write.
        Writes traces/<run_id>.jsonl and state/plans/<run_id>.json, prints WILL SEND / REFUSED.
  python run.py --scenario 01_clean_day12
        same, but the actor and verifier models draft and review (needs ANTHROPIC_API_KEY)
  python run.py --live --rehearse-only
        snapshot the real apps, rehearse, write trace + plan, send nothing
  python run.py --live
        full loop: rehearse -> Slack -> tap -> re-verify -> execute -> assert
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from owed.config import ROOT, today as today_fn  # noqa: E402
from owed.run import format_plan, load_mandate, rehearse_shadow, run  # noqa: E402,F401  (run: harness entry point)


def _scenario_path(name: str) -> Path:
    d = ROOT / "scenarios"
    p = d / (name if name.endswith(".json") else f"{name}.json")
    if p.exists():
        return p
    hits = sorted(d.glob(f"*{name}*.json"))
    if len(hits) != 1:
        raise SystemExit(f"scenario {name!r}: {'not found' if not hits else 'ambiguous ' + str([h.stem for h in hits])} in {d}")
    return hits[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", help="scenarios/<name>.json; shadow only")
    ap.add_argument("--live", action="store_true", help="use the live adapters")
    ap.add_argument("--rehearse-only", action="store_true", help="stop after rehearsal_done")
    ap.add_argument("--offline", action="store_true", help="no model calls: template draft, deterministic gate + verifier")
    ap.add_argument("--run-id", default=None)
    a = ap.parse_args(argv)
    run_id = a.run_id or datetime.now().strftime("run-%Y%m%d-%H%M%S")
    if a.offline:
        os.environ["OWED_OFFLINE"] = "1"

    if a.scenario:
        path = _scenario_path(a.scenario)
        scenario = json.loads(path.read_text())
        run_id = a.run_id or f"{path.stem}-{datetime.now().strftime('%H%M%S')}"
        plan = rehearse_shadow(scenario, run_id=run_id, offline=a.offline,
                               traces_dir=ROOT / "traces", state_dir=ROOT / "state")
        print()
        print(format_plan(plan))
        print(f"trace: traces/{run_id}.jsonl   plan: state/plans/{run_id}.json   live writes: 0")
        return 0
    if not a.live:
        ap.error("pass --scenario <name> or --live")

    from owed.adapters.calendar_live import GoogleCalendar
    from owed.adapters.chat_live import SlackChat
    from owed.adapters.inbox_live import GmailInbox
    from owed.adapters.ledger_live import StripeLedger

    drafter = None
    if a.offline:
        from owed.agent.drafter import template_draft
        drafter = template_draft
    plan = run(ledger=StripeLedger(), inbox=GmailInbox(), calendar=GoogleCalendar(), chat=SlackChat(),
               today=today_fn(), run_id=run_id, mandate=load_mandate(), drafter=drafter,
               state_dir=ROOT / "state", traces_dir=ROOT / "traces", rehearse_only=a.rehearse_only)
    print()
    print(format_plan(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
