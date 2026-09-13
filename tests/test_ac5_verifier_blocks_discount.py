"""AC5: an actor draft that offers a discount is refused by the verifier before any tap."""
from conftest import needs_agent

from evals import harness, scenario
from evals.harness import ScriptedDrafter

pytestmark = needs_agent


def test_deterministic_check_flags_discount():
    sc = scenario.load("10_verifier_discount")
    inv = sc.invoices[0]
    mandate = harness.load_mandate(sc.mandate)
    draft = ScriptedDrafter(sc.actor_draft)(inv, 2, mandate)
    assert draft["amount"] == inv.amount_due

    verifier = harness.agent_module("verifier")
    refusals = verifier.deterministic_checks(draft, inv, mandate)
    assert any(r.reason == "verifier_discount" for r in refusals), refusals


def test_ac5_verifier_blocks_discount(run_dirs):
    sc = scenario.load("10_verifier_discount")
    stubs = scenario.seed(sc)

    out = harness.run_scenario(sc, stubs, *run_dirs, drafter=ScriptedDrafter(sc.actor_draft))

    assert stubs.inbox.writes == 0
    assert "verifier_discount" in harness.refusal_reasons(out)
    assert harness.refused_before_tap(out.trace, "verifier_discount")
