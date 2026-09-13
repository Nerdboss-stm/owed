"""AC2: running twice on the same ledger state sends exactly one email per (invoice, step)."""
from conftest import needs_agent

from evals import harness, scenario
from evals.harness import ScriptedDrafter

pytestmark = needs_agent


def test_ac2_exactly_once(run_dirs):
    sc = scenario.load("09_run_twice")
    stubs = scenario.seed(sc)
    client = sc.invoices[0].client_email

    first = harness.run_scenario(sc, stubs, *run_dirs, run_id="run-1", drafter=ScriptedDrafter())
    assert stubs.inbox.writes == 1
    assert stubs.inbox.count_sent(client, "INV-0042") == 1
    assert "execute" in harness.phases(first.trace)

    second = harness.run_scenario(sc, stubs, *run_dirs, run_id="run-2", drafter=ScriptedDrafter())
    assert stubs.inbox.writes == 1, "second run sent again"
    assert stubs.inbox.count_sent(client, "INV-0042") == 1
    assert stubs.ledger.count_links("INV-0042") <= 1
    assert second.trace, "second run wrote no trace"
