"""PLAN 4:15 check: every scenario loads and seeds the stubs; stubs count writes."""
from datetime import datetime

import pytest

from evals import scenario as sc_mod
from evals.stubs import StubCalendar, StubChat, StubInbox, StubLedger
from owed.contract import LIVE_WRITES, Calendar, Chat, Inbox, Ledger

SPEC_ROWS = [
    "clean overdue, day 12",
    "paid between rehearsal and send",
    'client says "sent Friday"',
    "partial payment",
    "duplicate invoice numbers",
    "injected instruction in client email",
    "disputed invoice",
    "step 3 reached",
    "same run executed twice",
    "verifier catches discount in draft",
]

ALL = sc_mod.load_all()


def test_ten_scenarios_match_spec_table_rows():
    assert [s.row for s in ALL] == SPEC_ROWS
    assert len({s.name for s in ALL}) == 10


@pytest.mark.parametrize("sc", ALL, ids=[s.name for s in ALL])
def test_scenario_seeds_stubs(sc):
    stubs = sc_mod.seed(sc)
    assert isinstance(stubs.ledger, Ledger) and isinstance(stubs.ledger, StubLedger)
    assert isinstance(stubs.inbox, Inbox) and isinstance(stubs.inbox, StubInbox)
    assert isinstance(stubs.calendar, Calendar) and isinstance(stubs.calendar, StubCalendar)
    assert isinstance(stubs.chat, Chat) and isinstance(stubs.chat, StubChat)
    assert stubs.total_writes() == 0
    overdue = stubs.ledger.overdue(sc.today)
    assert overdue, "every scenario starts with at least one overdue invoice"
    for inv in overdue:
        assert inv.days_overdue(sc.today) > 0
        if inv.thread_id:
            assert stubs.inbox.thread(inv.thread_id), f"{inv.invoice_id} has an empty thread"


def test_stubs_count_every_write():
    stubs = sc_mod.seed(sc_mod.load("01_clean_day12"))
    before = LIVE_WRITES["count"]
    url = stubs.ledger.create_payment_link("INV-0042")
    stubs.ledger.mark_chased("INV-0042", 2)
    stubs.inbox.send("dana@northwind.test", "Invoice INV-0042", f"link {url}", "t-0042")
    stubs.calendar.create_event("OWED call INV-0042", datetime(2026, 9, 15, 10), "dana@northwind.test")
    stubs.chat.post("would send 1, refused 0")
    assert stubs.write_counts() == {"stripe": 2, "gmail": 1, "calendar": 1, "slack": 1}
    assert stubs.ledger.count_links("INV-0042") == 1
    assert stubs.inbox.count_sent("dana@northwind.test", "INV-0042") == 1
    assert stubs.calendar.count_events("INV-0042") == 1
    assert stubs.chat.count_posts("would send") == 1
    assert stubs.ledger.get("INV-0042").last_chased_step == 2
    assert LIVE_WRITES["count"] == before, "stubs must never touch the contract live-write counter"


def test_reads_return_copies():
    stubs = sc_mod.seed(sc_mod.load("01_clean_day12"))
    inv = stubs.ledger.get("INV-0042")
    inv.paid = True
    assert stubs.ledger.get("INV-0042").paid is False
    with pytest.raises(KeyError):
        stubs.ledger.get("INV-9999")


def test_tap_fires_scenario_events():
    sc = sc_mod.load("02_paid_between")
    stubs = sc_mod.seed(sc)
    assert stubs.ledger.get("INV-0042").paid is False
    assert stubs.chat.wait_for_tap("1.000000") is True
    assert stubs.ledger.get("INV-0042").paid is True
    assert stubs.ledger.writes == 0, "money arriving is not an agent write"
    assert stubs.ledger.overdue(sc.today) == []


def test_duplicate_rows_share_an_id():
    stubs = sc_mod.seed(sc_mod.load("05_duplicate_invoice"))
    rows = stubs.ledger.overdue(sc_mod.load("05_duplicate_invoice").today)
    assert [r.invoice_id for r in rows] == ["INV-0042", "INV-0042"]
