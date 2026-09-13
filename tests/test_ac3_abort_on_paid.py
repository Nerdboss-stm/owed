"""AC3: ledger marked paid between rehearsal and execute -> intent aborted, Slack says ABORTED: paid."""
from conftest import needs_agent

from evals import harness, scenario
from evals.harness import ScriptedDrafter

pytestmark = needs_agent


def test_ac3_abort_on_paid(run_dirs):
    sc = scenario.load("02_paid_between")
    stubs = scenario.seed(sc)  # the tap marks INV-0042 paid before execute

    out = harness.run_scenario(sc, stubs, *run_dirs, drafter=ScriptedDrafter())

    assert stubs.chat.tap_calls >= 1, "agent never waited for the tap"
    assert stubs.ledger.get("INV-0042").paid is True
    assert stubs.inbox.writes == 0, "email sent to a client who already paid"
    assert stubs.chat.count_posts("ABORTED: paid") >= 1
    assert "abort" in harness.phases(out.trace)
