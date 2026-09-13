"""AC1: a rehearsal run produces zero live writes."""
from conftest import needs_agent

from evals import harness, scenario
from evals.harness import ScriptedDrafter
from owed.contract import LIVE_WRITES

pytestmark = needs_agent


def test_ac1_shadow_no_side_effects(run_dirs):
    sc = scenario.load("01_clean_day12")
    stubs = scenario.seed(sc)
    before = LIVE_WRITES["count"]

    out = harness.run_scenario(sc, stubs, *run_dirs, drafter=ScriptedDrafter(), rehearse_only=True)

    assert stubs.write_counts() == {"stripe": 0, "gmail": 0, "calendar": 0, "slack": 0}
    assert LIVE_WRITES["count"] == before
    phases = harness.phases(out.trace)
    assert "rehearsal_done" in phases
    assert not {"posted_plan", "tap", "execute"} & set(phases)
    assert out.plan is not None and out.plan["intents"], "rehearsal planned nothing"
