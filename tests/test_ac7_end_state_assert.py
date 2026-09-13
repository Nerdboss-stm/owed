"""AC7: after execute the asserter finds exactly the expected counts in each app, and raises otherwise."""
from datetime import datetime

import pytest
from conftest import needs_agent

from evals import harness, scenario
from evals.harness import ScriptedDrafter

pytestmark = needs_agent


def _run(name, run_dirs, **kw):
    sc = scenario.load(name)
    stubs = scenario.seed(sc)
    out = harness.run_scenario(sc, stubs, *run_dirs, drafter=ScriptedDrafter(), **kw)
    assert "assert" in harness.phases(out.trace)
    assert out.plan is not None, "run wrote no plan file"
    return stubs, harness.plan_from_json(out.plan)


def test_ac7_end_state_matches_after_execute(run_dirs):
    stubs, plan = _run("08_step3_tap", run_dirs, tap=True)
    asserter = harness.agent_module("asserter")
    states = asserter.assert_end_state(plan, **stubs.as_kwargs())
    assert states and all(s.ok for s in states), states
    assert {"calendar", "slack"} <= {s.app for s in states}


def test_ac7_raises_when_counts_differ(run_dirs):
    stubs, plan = _run("08_step3_tap", run_dirs, tap=True)
    asserter = harness.agent_module("asserter")
    stubs.calendar.create_event("OWED call INV-0042", datetime(2026, 9, 18, 10), "dana@northwind.test")
    with pytest.raises(AssertionError):
        asserter.assert_end_state(plan, **stubs.as_kwargs())


def test_ac7_raises_on_extra_email(run_dirs):
    stubs, plan = _run("01_clean_day12", run_dirs)
    asserter = harness.agent_module("asserter")
    assert stubs.inbox.writes == 1
    sent = [i for i in plan.intents if i.kind == "send_email"]
    assert len(sent) == 1, plan.intents
    payload = sent[0].payload
    stubs.inbox.send(payload["to"], payload["subject"], "sent outside the executor", payload.get("thread_id"))
    with pytest.raises(AssertionError):
        asserter.assert_end_state(plan, **stubs.as_kwargs())
