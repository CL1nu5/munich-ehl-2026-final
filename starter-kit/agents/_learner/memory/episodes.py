"""Episode Extractor: the finished cases of this run, read from its journal (read-only).

An episode joins what the harness recorded (result, score, cost breakdown, supplier reasons, every
supplier reply) with what our agent recorded in its own state (tactics, predictions, lessons used,
its closing reflection).
"""

import json
from urllib.parse import unquote

from .runlocate import connect

REASONS = 12


def signature(run_dir):
    """Cheap fingerprint of which cases have finished - re-extract only when it changes."""
    con = connect(run_dir)
    try:
        rows = con.execute(
            "SELECT id, json_extract(value, '$.result') IS NOT NULL, "
            "coalesce(json_extract(value, '$.agent_state.closed'), 0) FROM cases ORDER BY id"
        ).fetchall()
    finally:
        con.close()
    return tuple((r[0], bool(r[1]), bool(r[2])) for r in rows if r[1] or r[2])


def _replies(con, session_id):
    """Supplier replies to our messages, per supplier, in order: [{status, price, claims}]."""
    out = {}
    if not session_id:
        return out
    prefix = f"sessions/{session_id}/threads/"
    rows = con.execute(
        "SELECT path, body, response FROM operations WHERE status='done' AND path LIKE ? ORDER BY created",
        ("sessions/%/threads/%/messages",),
    ).fetchall()
    for path, body, response in rows:
        path = unquote(path)
        if not path.startswith(prefix):
            continue
        sid = path[len(prefix) : -len("/messages")]
        try:
            reply = json.loads(response or "{}")
            sent = json.loads(body or "{}").get("message") or {}
        except ValueError:
            continue
        if sent.get("action"):
            continue
        sheets = reply.get("term_sheets") or [{}]
        out.setdefault(sid, []).append(
            {
                "status": reply.get("status") or ("ended" if reply.get("ended") or "result" in reply else None),
                "price": (sheets[0].get("terms") or {}).get("unit_price"),
                "claims": [r.get("result") for r in reply.get("claim_results") or []],
                "refs": sent.get("refs") or [],
                "our_prices": [(s.get("terms") or {}).get("unit_price") for s in sent.get("term_sheets") or []],
            }
        )
    return out


def extract(run_dir, exclude=None):
    """[episode] for every case of the run that has ended or that our agent has closed."""
    con = connect(run_dir)
    try:
        rows = con.execute("SELECT id, value FROM cases ORDER BY id").fetchall()
        episodes = []
        for cid, value in rows:
            if cid == exclude:
                continue
            try:
                rec = json.loads(value)
            except ValueError:
                continue
            st = rec.get("agent_state") or {}
            result = rec.get("result")
            if not result and not st.get("closed"):
                continue
            case = rec.get("case") or {}
            sups = st.get("sup") or {}
            trace = (result or {}).get("trace") or []
            episodes.append(
                {
                    "case_id": cid,
                    "type": case.get("type"),
                    "goal": (case.get("buyer") or {}).get("category_goal"),
                    "has_alternative": ((case.get("buyer") or {}).get("alternative") or {}).get("kind")
                    not in (None, "none"),
                    "suppliers": {
                        s: {"tags": (sups.get(s) or {}).get("tags") or []} for s in case.get("suppliers") or []
                    },
                    "result": {
                        k: (result or {}).get(k)
                        for k in (
                            "deal",
                            "supplier_id",
                            "score",
                            "c_open",
                            "c_deal",
                            "c_best",
                            "c_alt",
                            "must_haves_ok",
                            "cost_breakdown",
                        )
                    }
                    if result
                    else None,
                    "end_reason": rec.get("end_reason"),
                    "reasons": [
                        str(t.get("reasons"))[:300] for t in trace if t.get("actor") != "buyer" and t.get("reasons")
                    ][-REASONS:],
                    "replies": _replies(con, rec.get("session_id")),
                    "opening": {s: (sups.get(s) or {}).get("open_price") for s in case.get("suppliers") or []},
                    "log": st.get("log") or [],
                    "reflection": st.get("reflection") or {},
                    "close": st.get("close") or {},
                }
            )
        return episodes
    finally:
        con.close()
