"""Be the client: a judge enters their email, OWED seeds a test invoice for them, rehearses, and (after
their Approve) sends them the real chase email. Runs on THIS machine, never on Vercel.

  ~/.venvs/owed/bin/python ui/client_server.py            # http://localhost:8766
  cloudflared tunnel --url http://localhost:8766          # public URL; put it in public/client.json

POST /client/start   {email, slack_webhook?}  seed -> rehearse_for -> post plan -> {run_id, plan}
POST /client/approve {email, run_id}          write approve.json for that email; execute_for runs in the background
GET  /client/status?email=                    latest run, run history for its invoice, trace, end state, CLOSED line
GET  /, /client                               public/client.html (the same page Vercel serves at /client)

State: state/clients/<email>/{latest.json, meta/<run_id>.json, plans/, traces/, approve.json, posts.jsonl,
sent.json, closed.json}. Credentials come from .env exactly as run.py --live. Every live write still goes
through owed.agent.executor; the web Approve is the tap via owed.adapters.chat_webtap.WebTapChat.
CORS is open because the Vercel page calls this backend cross-origin; it is a sandbox with no accounts.
"""
from __future__ import annotations
import json
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from owed.adapters.chat_webtap import WEBHOOK_PREFIX, WebTapChat  # noqa: E402
from owed.config import ROOT, today as today_fn  # noqa: E402
from owed.run import client_dir, execute_for, plan_summary, rehearse_for, send_receipt_for  # noqa: E402

STATE_ROOT = ROOT / "state"
PUBLIC = ROOT / "public"
RUN_ID_RE = re.compile(r"^client-[A-Za-z0-9\-]{1,40}-\d{6}$")
CLOSED_CHECK_EVERY_S = 10
RUN_LOCK = threading.Lock()          # one live flow at a time; the Gmail client is not thread-safe
_closed_cache: dict[str, tuple[float, Optional[dict]]] = {}
_ledger = None
_inbox = None

app = FastAPI(title="OWED · Be the client")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


# ---------- live adapters, built once ----------

def ledger():
    global _ledger
    if _ledger is None:
        from owed.adapters.ledger_live import StripeLedger
        _ledger = StripeLedger()
    return _ledger


def inbox():
    global _inbox
    if _inbox is None:
        from owed.adapters.inbox_live import GmailInbox
        _inbox = GmailInbox()
    return _inbox


# ---------- per-client state ----------

def _dir(email: str) -> Path:
    try:
        d = client_dir(email, STATE_ROOT)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(path: Path) -> Optional[dict]:
    return json.loads(path.read_text()) if path.exists() else None


def _meta_path(d: Path, run_id: str) -> Path:
    return d / "meta" / f"{run_id}.json"


def _set_meta(d: Path, run_id: str, **fields) -> dict:
    p = _meta_path(d, run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = _read(p) or {"run_id": run_id}
    meta.update(fields)
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    p.write_text(json.dumps(meta, indent=2, default=str))
    return meta


def _latest(d: Path) -> Optional[dict]:
    latest = _read(d / "latest.json")
    if not latest:
        return None
    return _read(_meta_path(d, latest["run_id"])) or latest


def _chat(d: Path, run_id: str, webhook: Optional[str]) -> WebTapChat:
    return WebTapChat(d / "posts.jsonl", d / "approve.json", run_id, webhook)


def _trace_lines(d: Path, run_id: str) -> list[dict]:
    p = d / "traces" / f"{run_id}.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _run_history(d: Path, invoice_id: Optional[str]) -> list[dict]:
    """Every run for this invoice, oldest first: what the timeline is built from."""
    out = []
    for p in sorted((d / "meta").glob("*.json")) if (d / "meta").exists() else []:
        m = _read(p) or {}
        if invoice_id and (m.get("invoice") or {}).get("invoice_id") != invoice_id:
            continue
        trace = _trace_lines(d, m["run_id"])
        plan = _read(d / "plans" / f"{m['run_id']}.json") or {}
        state = next((t["data"].get("state") for t in trace if t["phase"] == "classify" and t.get("data")), None)
        out.append({
            "run_id": m["run_id"], "phase": m.get("phase"), "started_at": m.get("started_at"),
            "approved_at": m.get("approved_at"), "summary": m.get("summary"), "thread_state": state,
            "refusals": plan.get("refusals", []), "intents": len(plan.get("intents", [])),
            "sent": any(t["phase"] == "execute" and t["decision"].startswith("SENT") for t in trace),
            "aborted": any(t["phase"] == "abort" for t in trace),
        })
    out.sort(key=lambda r: r.get("started_at") or "")
    return out


def _closed_line(email: str, invoice_id: Optional[str], d: Path) -> Optional[dict]:
    """'CLOSED INV-XXXX: $3,400.00 received' once the ledger shows the invoice paid, or its payment link
    has a completed checkout. Checked every 10 s."""
    if not invoice_id:
        return None
    persisted = _read(d / "closed.json")
    if persisted and persisted.get("invoice_id") == invoice_id:
        return persisted
    now = time.monotonic()
    cached = _closed_cache.get(email)
    if cached and now - cached[0] < CLOSED_CHECK_EVERY_S:
        return cached[1]
    result: Optional[dict] = None
    try:
        inv = ledger().get(invoice_id)
        received = 0.0
        if inv.paid or inv.amount_due <= 0:
            received = inv.amount_total
        elif hasattr(ledger(), "paid_via_link"):
            received = ledger().paid_via_link(invoice_id)  # the link is its own Stripe object; see ledger_live
        if received > 0:
            result = {"invoice_id": invoice_id, "amount": received, "at": datetime.now(timezone.utc).isoformat(),
                      "line": f"CLOSED {invoice_id}: ${received:,.2f} received"}
            (d / "closed.json").write_text(json.dumps(result, indent=2))
        else:
            result = {"invoice_id": invoice_id, "amount_due": inv.amount_due, "line": None}
    except Exception as e:  # noqa: BLE001 - a read failure is reported in the panel, not hidden
        result = {"invoice_id": invoice_id, "line": None, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    _closed_cache[email] = (now, result)
    return result


# ---------- background work ----------

def _execute_in_background(email: str, d: Path, run_id: str, meta: dict) -> None:
    with RUN_LOCK:
        try:
            plan = execute_for(email, run_id, d, ledger=ledger(), inbox=inbox(), calendar=None,
                               chat=_chat(d, run_id, meta.get("webhook_url")), plan_ts=meta.get("plan_ts", ""))
            _set_meta(d, run_id, phase="executed", summary=plan_summary(plan),
                      finished_at=datetime.now(timezone.utc).isoformat())
        except Exception as e:  # noqa: BLE001 - surfaced in the panel; the trace already has the FAILED line
            traceback.print_exc()
            _set_meta(d, run_id, phase="failed", error=f"{type(e).__name__}: {str(e)[:300]}")


def _receipt_in_background(email: str, d: Path, run_id: str, invoice_id: str, webhook: Optional[str]) -> None:
    with RUN_LOCK:
        try:
            send_receipt_for(email, run_id, invoice_id, d, ledger=ledger(), inbox=inbox(),
                             chat=_chat(d, run_id, webhook))
            _set_meta(d, run_id, receipt_sent=True, receipt_at=datetime.now(timezone.utc).isoformat())
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            _set_meta(d, run_id, receipt_error=f"{type(e).__name__}: {str(e)[:300]}")


# ---------- endpoints ----------

class StartBody(BaseModel):
    email: str
    slack_webhook: Optional[str] = None


class ApproveBody(BaseModel):
    email: str
    run_id: str


@app.get("/")
@app.get("/client")
def page():
    return FileResponse(PUBLIC / "client.html")


@app.get("/owed.css")
def stylesheet():
    return FileResponse(PUBLIC / "owed.css", media_type="text/css")


@app.post("/client/start")
def start(body: StartBody):
    email = body.email.strip().lower()
    d = _dir(email)
    webhook = (body.slack_webhook or "").strip() or None
    if webhook and not webhook.startswith(WEBHOOK_PREFIX):
        raise HTTPException(400, f"slack_webhook must start with {WEBHOOK_PREFIX}")
    if not RUN_LOCK.acquire(timeout=1):
        raise HTTPException(409, "another run is in progress; try again in a moment")
    try:
        from owed.seed import seed_invoice
        seeded = seed_invoice(email)
        run_id = f"client-{seeded['invoice_id']}-{datetime.now().strftime('%H%M%S')}"
        _set_meta(d, run_id, email=email, phase="rehearsing", invoice=seeded, webhook=bool(webhook),
                  webhook_url=webhook, started_at=datetime.now(timezone.utc).isoformat())
        (d / "latest.json").write_text(json.dumps({"run_id": run_id, "email": email}))
        try:
            plan, ts = rehearse_for(email, d, ledger=ledger(), inbox=inbox(), calendar=None, today=today_fn(),
                                    chat=_chat(d, run_id, webhook), run_id=run_id)
        except Exception as e:  # noqa: BLE001 - recorded for the panel, then re-raised as a 500
            _set_meta(d, run_id, phase="failed", error=f"{type(e).__name__}: {str(e)[:300]}")
            traceback.print_exc()
            raise HTTPException(500, f"rehearsal failed: {type(e).__name__}: {str(e)[:300]}") from e
        meta = _set_meta(d, run_id, phase="rehearsed", plan_ts=ts, summary=plan_summary(plan))
    finally:
        RUN_LOCK.release()
    return {"run_id": run_id, "email": email, "invoice": seeded, "phase": meta["phase"],
            "summary": meta["summary"], "plan": json.loads(plan.to_json())}


@app.post("/client/approve")
def approve(body: ApproveBody):
    """The tap. run_id must be one that /client/start returned for this same email: the meta file
    lives under that email's directory, so a run_id from another email is a 404. The flag is written
    only inside that directory, as state/clients/<email>/approve.json naming the run."""
    email = body.email.strip().lower()
    d = _dir(email)
    run_id = body.run_id.strip()
    if not RUN_ID_RE.match(run_id):
        raise HTTPException(400, "run_id must be the value returned by /client/start")
    meta = _read(_meta_path(d, run_id))
    if not meta or meta.get("email") != email:
        raise HTTPException(404, f"no run {run_id} for {email}")
    if meta.get("phase") in ("executing", "executed"):
        raise HTTPException(409, f"run {run_id} is already {meta['phase']}")
    if meta.get("phase") != "rehearsed":
        raise HTTPException(409, f"run {run_id} is {meta.get('phase')}, not rehearsed")
    (d / "approve.json").write_text(json.dumps({"run_id": run_id, "email": email, "via": "web",
                                               "approved_at": datetime.now(timezone.utc).isoformat()}, indent=2))
    meta = _set_meta(d, run_id, phase="executing", approved_at=datetime.now(timezone.utc).isoformat())
    threading.Thread(target=_execute_in_background, args=(email, d, run_id, meta), daemon=True).start()
    return {"approved": True, "run_id": run_id, "phase": "executing"}


@app.get("/client/status")
def status(email: str):
    email = email.strip().lower()
    d = _dir(email)
    meta = _latest(d)
    if not meta:
        return JSONResponse({"email": email, "phase": "none", "run_id": None, "runs": []},
                            headers={"Cache-Control": "no-store"})
    run_id = meta["run_id"]
    plan = _read(d / "plans" / f"{run_id}.json") or {}
    invoice = meta.get("invoice") or {}
    closed = _closed_line(email, invoice.get("invoice_id"), d)
    if closed and closed.get("line") and meta.get("phase") == "executed" \
            and not meta.get("receipt_sent") and not meta.get("receipt_pending"):
        meta = _set_meta(d, run_id, receipt_pending=True)
        threading.Thread(target=_receipt_in_background,
                         args=(email, d, run_id, invoice["invoice_id"], meta.get("webhook_url")), daemon=True).start()
    chat = _chat(d, run_id, None)
    return JSONResponse({
        "email": email, "run_id": run_id, "phase": meta.get("phase"), "error": meta.get("error"),
        "summary": meta.get("summary"), "invoice": invoice, "webhook": meta.get("webhook", False),
        "approved_at": meta.get("approved_at"), "started_at": meta.get("started_at"),
        "receipt_sent": bool(meta.get("receipt_sent")), "receipt_error": meta.get("receipt_error"),
        "runs": _run_history(d, invoice.get("invoice_id")),
        "plan": plan, "end_state": plan.get("end_state"), "trace": _trace_lines(d, run_id),
        "posts": chat.posts()[-6:], "closed": closed,
    }, headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    import uvicorn
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
