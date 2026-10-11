"""Read-only prefix replay of recorded choices; no simulated replies or counterfactual scores."""

import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import unquote

from ..docs import read_document
from .validation import documented
from .valuation import ranked_offers


def audit(folder):
    folder = Path(folder).resolve()
    con = sqlite3.connect((folder / "run.sqlite").as_uri() + "?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        records = {cid: json.loads(value) for cid, value in con.execute("SELECT id,value FROM cases")}
        operations = list(
            con.execute("SELECT path,body,response FROM operations WHERE status='done' ORDER BY created,rowid")
        )
        errors = {
            json.loads(v).get("attachment")
            for (v,) in con.execute("SELECT value FROM events WHERE kind='document_error'")
        }
    finally:
        con.close()
    sessions, choices = {}, []

    def hydrate(session, message):
        for attachment in message.get("attachments") or []:
            aid = attachment.get("attachment_id")
            if not aid or attachment.get("text") or aid in errors:
                continue
            cached = folder / "attachments" / hashlib.sha256((session + ":" + aid).encode()).hexdigest()
            if cached.is_file() and cached.stat().st_size <= 10 * 1024 * 1024:
                try:
                    attachment["text"] = read_document(attachment["name"], cached.read_bytes())
                except Exception:
                    attachment["error"] = "Unreadable cached document"
        return message

    for path, body, response in operations:
        sent, reply = json.loads(body), json.loads(response or "{}")
        if path == "sessions":
            session, cid = reply.get("session_id"), sent.get("case_id")
            if session and cid in records:
                # `threads` holds the quote e-mails with term sheets and the quote PDF; `opening` is the
                # structured /moves form without them (kept as the fallback for older journals).
                opening = reply.get("threads") or {s: [m] for s, m in (reply.get("opening") or {}).items()}
                sessions[session] = (
                    cid,
                    {sid: [hydrate(session, msg) for msg in msgs] for sid, msgs in opening.items()},
                )
            continue
        parts = unquote(path).split("/")
        if len(parts) != 5 or parts[0] != "sessions" or parts[2] != "threads" or parts[4] != "messages":
            continue
        session, supplier = parts[1], parts[3]
        if session not in sessions:
            continue
        cid, threads = sessions[session]
        case, message = records[cid]["case"], sent.get("message") or {}
        action = message.get("action") or {}
        if action.get("type") == "accept":
            events = threads.get(supplier) or []
            candidates = ranked_offers(case, events) if events and documented(events) else []
            eligible = [s for s in candidates if s["beats_alternative"]]
            accepted = next((s for s in candidates if s["term_sheet_id"] == action.get("term_sheet_id")), None)
            best = eligible[0] if eligible else None
            choices.append(
                {
                    "case_id": cid,
                    "supplier_id": supplier,
                    "accepted_id": action.get("term_sheet_id"),
                    "accepted_known_cost": accepted["known_cost"] if accepted else None,
                    "best_current_id": best["term_sheet_id"] if best else None,
                    "best_current_known_cost": best["known_cost"] if best else None,
                    "available_cost_difference": round(accepted["known_cost"] - best["known_cost"], 2)
                    if accepted and best
                    else None,
                    "current_offers": [
                        {k: s[k] for k in ("term_sheet_id", "known_cost", "beats_alternative")} for s in candidates
                    ],
                }
            )
        if not action and reply.get("term_sheets"):
            threads.setdefault(supplier, []).extend([{**message, "from": "buyer"}, hydrate(session, reply)])
        elif not action and reply.get("status"):
            # A newer message without an offer supersedes old offers too.
            threads.setdefault(supplier, []).extend([{**message, "from": "buyer"}, hydrate(session, reply)])
    return {
        "run_id": folder.name,
        "mode": "read-only recorded-prefix replay",
        "limitation": (
            "Known buyer costs only. No hidden relationship cost, new supplier replies or counterfactual score."
        ),
        "cases_with_results": sum(bool(c.get("result")) for c in records.values()),
        "acceptances": choices,
        "missed_current_alternatives": sum((c["available_cost_difference"] or 0) > 0.01 for c in choices),
    }
