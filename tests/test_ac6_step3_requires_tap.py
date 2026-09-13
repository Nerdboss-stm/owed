"""AC6: a step-3 intent never executes without a white_check_mark reaction."""
from conftest import needs_agent

from evals import harness, scenario
from evals.harness import ScriptedDrafter

pytestmark = needs_agent


def test_ac6_no_tap_no_event(run_dirs):
    sc = scenario.load("08_step3_tap")
    stubs = scenario.seed(sc)

    harness.run_scenario(sc, stubs, *run_dirs, drafter=ScriptedDrafter(), tap=False)

    assert stubs.chat.tap_calls >= 1, "agent never waited for the tap"
    assert stubs.calendar.writes == 0
    assert stubs.inbox.writes == 0
    assert stubs.ledger.writes == 0


def test_ac6_tap_then_one_event(run_dirs):
    sc = scenario.load("08_step3_tap")
    stubs = scenario.seed(sc)

    harness.run_scenario(sc, stubs, *run_dirs, drafter=ScriptedDrafter(), tap=True)

    assert stubs.chat.tap_calls >= 1
    assert stubs.calendar.writes == 1
    assert stubs.calendar.count_events("INV-0042") == 1
