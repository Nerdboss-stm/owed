"""
Run every scenario through the agent against in-memory stubs and write the
reliability table into README.md between <!-- EVALS:START --> and <!-- EVALS:END -->.

    python evals/run_evals.py                 # all scenarios, real drafter if ANTHROPIC_API_KEY is set
    python evals/run_evals.py --offline       # template drafts, no model calls
    python evals/run_evals.py --only 01_clean_day12 --stdout

Traces land in traces/<run_id>.jsonl. Per-scenario state (sent.json, plans) lives
under state/evals/<scenario>/ and is wiped before each scenario.
Exit code: 0 all pass, 1 any fail, 2 agent not merged.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals import harness  # noqa: E402
from evals.checks import Verdict, evaluate  # noqa: E402
from evals.harness import ScriptedDrafter  # noqa: E402
from evals.scenario import SCENARIO_DIR, Scenario, load_all, seed  # noqa: E402

START = "<!-- EVALS:START -->"
END = "<!-- EVALS:END -->"


@dataclass
class Row:
    scenario: Scenario
    verdict: Optional[Verdict]   # None = not run
    note: str = ""


def pick_drafter(scenario: Scenario, mode: str):
    if mode == "offline":
        return ScriptedDrafter(scenario.actor_draft)
    if scenario.actor_draft:
        return ScriptedDrafter(scenario.actor_draft, fallback=harness.real_drafter())
    return None  # run.py uses its own actor


def run_one(scenario: Scenario, mode: str, traces_dir: Path, state_root: Path, stamp: str) -> Verdict:
    state_dir = state_root / scenario.name
    if state_dir.exists():
        shutil.rmtree(state_dir)
    stubs = seed(scenario)
    drafter = pick_drafter(scenario, mode)
    outcomes = []
    for n in range(scenario.runs):
        outcomes.append(
            harness.run_scenario(
                scenario, stubs, state_dir, traces_dir,
                run_id=f"eval-{scenario.name}-{n + 1}-{stamp}",
                drafter=drafter,
            )
        )
    return evaluate(scenario, stubs, outcomes)


def has_api_key() -> bool:
    """ANTHROPIC_API_KEY exported, or set in the gitignored .env next to run.py."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    env = ROOT / ".env"
    if not env.exists():
        return False
    for line in env.read_text().splitlines():
        k, _, v = line.partition("=")
        if k.strip() == "ANTHROPIC_API_KEY" and v.strip():
            return True
    return False


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render_table(rows: list[Row], mode: str, stamp: str) -> str:
    out = ["| Scenario | Expected | Actual | End state checked | Pass |", "|---|---|---|---|---|"]
    passed = 0
    for r in rows:
        if r.verdict is None:
            actual, mark = "", f"not run: {r.note}"
        elif r.verdict.passed:
            actual, mark = r.verdict.actual, "✅"
            passed += 1
        else:
            actual, mark = r.verdict.actual, f"❌ {r.verdict.cause}"
        out.append(
            f"| {_cell(r.scenario.row)} | {_cell(r.scenario.expected)} | {_cell(actual)} "
            f"| {_cell(r.scenario.end_state)} | {_cell(mark)} |"
        )
    out.append("")
    out.append(f"_Last eval run {stamp} ({mode} drafts): {passed}/{len(rows)} pass._")
    return "\n".join(out)


def write_readme(readme: Path, table: str) -> None:
    text = readme.read_text()
    a, b = text.find(START), text.find(END)
    if a < 0 or b < 0 or b < a:
        raise ValueError(f"{readme} lacks {START} / {END} markers")
    new = text[: a + len(START)] + "\n" + table + "\n" + text[b:]
    readme.write_text(new)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--readme", type=Path, default=ROOT / "README.md")
    ap.add_argument("--scenarios", type=Path, default=SCENARIO_DIR)
    ap.add_argument("--only", action="append", default=[], help="scenario name; repeatable")
    ap.add_argument("--offline", action="store_true", help="template drafts, no model calls")
    ap.add_argument("--stdout", action="store_true", help="print the table instead of editing README")
    ap.add_argument("--traces", type=Path, default=ROOT / "traces")
    ap.add_argument("--state", type=Path, default=ROOT / "state" / "evals")
    args = ap.parse_args(argv)

    scenarios = load_all(args.scenarios)
    if args.only:
        scenarios = [s for s in scenarios if s.name in args.only]
        missing = set(args.only) - {s.name for s in scenarios}
        if missing:
            raise SystemExit(f"unknown scenarios: {sorted(missing)}")

    mode = "offline" if args.offline or not has_api_key() else "online"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    run_stamp = datetime.now().strftime("%H%M%S")
    agent_ok = harness.agent_available()
    rows: list[Row] = []

    for sc in scenarios:
        if not agent_ok:
            rows.append(Row(sc, None, harness.missing_reason()))
            print(f"SKIP {sc.name}: {harness.missing_reason()}")
            continue
        try:
            verdict = run_one(sc, mode, args.traces, args.state, run_stamp)
        except Exception as exc:  # reported in the table, never hidden
            traceback.print_exc()
            verdict = Verdict(False, "run crashed", f"{type(exc).__name__}: {exc}")
        rows.append(Row(sc, verdict))
        tag = "PASS" if verdict.passed else "FAIL"
        print(f"{tag} {sc.name}: {verdict.actual}" + (f" -- {verdict.cause}" if verdict.cause else ""))

    table = render_table(rows, mode, stamp)
    if args.stdout:
        print(table)
    else:
        write_readme(args.readme, table)
        print(f"wrote table to {args.readme}")

    if not agent_ok:
        return 2
    return 0 if all(r.verdict and r.verdict.passed for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
