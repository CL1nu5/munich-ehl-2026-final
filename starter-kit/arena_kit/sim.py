"""arena-sim: a minimal arena that speaks the public API exactly - to learn the contract.

Same paths, fields, statuses, errors and limits as the hosted arena; quote PDFs and cost-breakdown
XLSX files in the same format. Its supplier, though, is one plain script for every case and NOT
how the arena's suppliers behave: it opens 10 % above your current price, concedes a fixed 2 % of
its opening per message with a term sheet down to 90 %, agrees when you meet its price, sends a
final offer from round 5 and walks away if you push after that. It checks cited data only for
whether the ID exists. The simulator teaches the contract; the real suppliers think for themselves.

    arena-sim serve [--port 8000] [--data <data package>] [--event-code sim] [--no-clock]

--no-clock switches the 60-second rule off (threads wait forever) to try the API by hand.
"""

import argparse
import datetime
import hashlib
import html
import io
import itertools
import json
import re
import threading
import time
from pathlib import Path

from .cost import closing_terms, cost_breakdown, must_haves_ok, score, total_cost
from .docs import LABELS

CASES = Path(__file__).parent / "sim_cases"
MOVE_S, MAX_OPEN, MAX_CALLS, MAX_BODY = 60, 10, 10, 64_000
PRACTICE_LIMIT, DAY_S, REMAINING = 200, 86_400, "X-Practice-Remaining"  # the arena's practice quota
OPEN_UP, STEP, FLOOR, FINAL_ROUND = 0.10, 0.02, 0.90, 5
ID_RE = re.compile(r"\b(?:idx|quote|offer|sc|qn|po|cost|fc|news|doc|ctr):[\w.:@#/-]*\w")
LATE = "late: more than 60 s after the supplier's last move, so it counted as a timeout"
NAMES = {field: label for label, field in LABELS.items()}
SIGNALS = {"reply_delay_days": 1, "validity_days": 30}


def _fmt(v):
    return "none" if v is None else f"{v:.2f}" if isinstance(v, float) else str(v)


def pdf(title, sections):
    """A one-page PDF of text lines, labelled like the arena's quotes."""
    lines = [title]
    for head, terms in sections:
        lines += [head, *(f"{NAMES.get(k, k)} {_fmt(v)}" for k, v in terms.items())]
    esc = [x.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for x in lines]
    text = "".join(f"BT /F1 10 Tf 50 {800 - 14 * i} Td ({t}) Tj ET\n" for i, t in enumerate(esc))
    page = "/MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R {page} >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(text)} >>\nstream\n{text}endstream",
    ]
    out, offsets = "%PDF-1.4\n", []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n"
    xref = f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    xref += "".join(f"{o:010d} 00000 n \n" for o in offsets)
    tail = f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{len(out)}\n%%EOF\n"
    return (out + xref + tail).encode("latin-1", "replace")


def xlsx(price):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Cost breakdown"
    ws.append(["Element", "EUR per unit", "Share of price"])
    for i, (name, share) in enumerate(
        [("Material", 0.5), ("Conversion", 0.3), ("Overhead", 0.15), ("Margin", 0.05)], 2
    ):
        ws.append([name, round(price * share, 2), f"=B{i}/B6"])
    ws.append(["Unit price", "=SUM(B2:B5)", "=SUM(C2:C5)"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def structured(msg, round_):
    """A supplier e-mail in the structured shape of the /moves path."""
    sheet = (msg["term_sheets"] or [{}])[0]
    return {
        "offer_id": sheet.get("term_sheet_id"),
        "round": round_,
        "status": msg["status"],
        "terms": sheet.get("terms", {}),
        "missing_fields": msg["missing_fields"],
        "claims": [],
        "claim_results": msg["claim_results"],
        "signals": SIGNALS,
        "text": msg["body"],
    }


class Fail(Exception):
    def __init__(self, status, detail, headers=None):
        self.status, self.detail, self.headers = status, detail, headers


class Sim:
    """The arena's state and its one scripted supplier. One lock serialises every call."""

    def __init__(self, cases, facts=None, event_code="sim", clock=time.time, no_clock=False):
        self.cases, self.facts, self.code, self.clock = cases, facts or {}, event_code, clock
        self.no_clock = no_clock  # no 60-second rule: to try the API by hand
        self.teams, self.sessions, self.replies, self.calls = {}, {}, {}, {}
        self.ids, self.lock = itertools.count(1), threading.Lock()

    # -- the scripted supplier -----------------------------------------------------------------
    def _mail(self, s, th, status, stated, text, files=()):
        th["n"] += 1
        th.update(status=status, stated=stated, offer_id=f"offer:{th['key']}-{th['round']}")
        s["offers"][th["offer_id"]] = stated
        sheets = [] if status == "walked_away" else [{"term_sheet_id": th["offer_id"], "terms": stated}]
        msg = {
            "thread_id": f"thread:{s['case']['case_id'][5:]}:{th['key']}",
            "message_id": f"msg:{th['key']}-{th['round']}-{th['n']}",
            "in_reply_to": None,
            "from": f"sales@{th['key']}",
            "subject": f"Re: {s['case']['title']}",
            "body": f"Simulated supplier. {text}",
            "refs": [],
            "action": None,
            "term_sheets": sheets,
            "status": status,
            "signals": SIGNALS,
            "claim_results": [],
            "missing_fields": sorted(set(s["case"]["negotiable"]) - set(stated)),
            "attachments": [],
        }
        for kind in files:
            att = f"att:{th['key']}-{th['round']}-{kind.split('.')[0]}"
            s["files"][att] = (kind, dict(stated), dict(th["standard"]))
            msg["attachments"].append(
                {"attachment_id": att, "name": kind, "type": kind[-4:].strip("."), "content_b64": None}
            )
        th["messages"].append(msg)
        return msg

    def _answer(self, s, th, msg, sheets):
        results = []
        for i, ref in enumerate(list(dict.fromkeys(msg.get("refs", []) + ID_RE.findall(msg.get("body", ""))))[:3]):
            known = ref in self.facts or ref in s["case"].get("facts", {}) or ref in s["offers"]
            # a rival's quote may be cited to every bidder: it counts once per supplier, the rest once per case
            seen = (th["key"], ref) if ref.startswith(("quote:", "offer:")) else ref
            result = "contradicted" if not known else "duplicate" if seen in s["used"] else "verified"
            results.append({"index": i, "result": result})
            if known:
                s["used"].add(seen)
        files = ["cost-breakdown.xlsx"] if "breakdown" in msg.get("body", "").lower() else []
        price = round(max(th["opening"] * FLOOR, th["stated"]["unit_price"] - th["opening"] * STEP), 2)
        best = max(sheets, key=lambda t: t["unit_price"]) if sheets else None
        if best is None:
            reply = self._mail(s, th, th["status"], th["stated"], "We keep our offer.", files)
        elif best["unit_price"] >= price:
            reply = self._mail(s, th, "agreed", best, "We agree.", [*files, "quote.pdf"])
        elif th["status"] == "final_offer":
            th["closed"] = "walked_away"
            reply = self._mail(s, th, "walked_away", th["stated"], "We end here.")
        elif th["round"] >= FINAL_ROUND:
            reply = self._mail(s, th, "final_offer", {"unit_price": price}, "Final offer.", [*files, "quote.pdf"])
        else:
            reply = self._mail(s, th, "open", {"unit_price": price}, "Our counter-offer.", files)
        reply["claim_results"] = results
        s["trace"].append(
            {
                "round": th["round"],
                "actor": f"sup:{th['key']}",
                "move": structured(reply, th["round"]),
                "reasons": ["A fixed script: it concedes the same step per term sheet, down to its floor."],
            }  # the practice shape: the move and the supplier's reasons in words
        )
        return reply

    def _check(self, case, terms, th):
        if set(terms) != set(case["negotiable"]) or not (terms.get("unit_price") or 0) > 0:
            raise Fail(422, f"a term sheet carries exactly the negotiable fields {case['negotiable']}")
        for f, v in terms.items():
            options = case["term_options"].get(f)
            if f != "unit_price" and options and v not in options and v != th["standard"].get(f):
                raise Fail(422, f"{f}={v!r} is not one of {options}")
        return terms

    # -- sessions ------------------------------------------------------------------------------
    def open(self, team, case_id):
        case = self.cases.get(case_id)
        if case is None:
            raise Fail(404, "no such case")
        if sum(s["status"] == "open" for s in self._mine(team)) >= MAX_OPEN:
            raise Fail(429, f"at most {MAX_OPEN} open sessions per key")
        if (p := self.practice(team))["left"] == 0:  # no Retry-After: retrying does not help
            raise Fail(429, f"practice limit: {PRACTICE_LIMIT} sessions per team in 24 hours; resets_at "
                       f"{p['resets_at']}", {REMAINING: "0"})  # fmt: skip
        sid, current = f"sim{next(self.ids)}", case["buyer"].get("current_unit_price")
        s = {
            "id": sid,
            "team": team,
            "case": case,
            "status": "open",
            "end_reason": None,
            "threads": {},
            "files": {},
            "offers": {},
            "used": set(),
            "result": None,
            "trace": [],
            "moves": [],
            "created_at": self.clock(),
            "late": set(),
        }
        for i, sup in enumerate(case["suppliers"]):
            opening = round((current or case["buyer"]["fallback_unit_price"]) * (1 + OPEN_UP + 0.02 * i), 2)
            standard = {f: (case["term_options"].get(f) or [None])[0] for f in case["negotiable"][1:]}
            th = s["threads"][sup] = {
                "key": sup.split(":", 1)[1],
                "round": 0,
                "n": 0,
                "timeouts": 0,
                "closed": None,
                "last_at": self.clock(),
                "messages": [],
                "late": [],
                "standard": standard,
                "opening": opening,
            }
            self._mail(s, th, "open", {"unit_price": opening}, "Our quote is attached.", ["quote.pdf"])
        self.sessions[sid] = s
        return {
            "session_id": sid,
            "case": case,
            "opening": {k: structured(th["messages"][0], 0) for k, th in s["threads"].items()},
            "threads": {k: list(th["messages"]) for k, th in s["threads"].items()},
        }

    def _mine(self, team):
        return [s for s in self.sessions.values() if s["team"] == team]

    def practice(self, team):
        """The team's practice quota in the rolling 24 hours, as GET /v1/me shows it."""
        day = sorted(s["created_at"] for s in self._mine(team) if s["created_at"] > self.clock() - DAY_S)
        frees = datetime.datetime.fromtimestamp(day[0] + DAY_S, datetime.UTC).isoformat() if day else None
        return {
            "used": len(day),
            "left": max(0, PRACTICE_LIMIT - len(day)),
            "limit": PRACTICE_LIMIT,
            "resets_at": frees,
        }

    def session(self, team, sid):
        s = self.sessions.get(sid)
        if s is None or s["team"] != team:
            raise Fail(404, "no such session")
        s["late"] = self._tick(s)
        return s

    def _tick(self, s):
        """The 60-second rule: a supplier left waiting counts a timeout - its offer stands; the
        third in a row (or one after the last round) ends the thread. Returns who just got one."""
        late = set()
        if self.no_clock:
            return late
        for sup, th in s["threads"].items():
            while s["status"] == "open" and not th["closed"] and self.clock() > th["last_at"] + MOVE_S:
                th["last_at"] += MOVE_S
                th["round"] += 1
                th["timeouts"] += 1
                th["late"].append({"offer_id": th["offer_id"], "status": th["status"], "terms": th["stated"]})
                if th["timeouts"] >= 3 or th["round"] >= s["case"]["round_limit"]:
                    th["closed"] = "timeout"
                late.add(sup)
        self._settle(s, "timeout")
        return late

    def _settle(self, s, reason):
        if s["status"] == "open" and all(th["closed"] for th in s["threads"].values()):
            self._end(s, reason, None)

    def _end(self, s, reason, sup):
        case, ths = s["case"], s["threads"]
        c_open = min(total_cost(case, {"unit_price": th["opening"]}, th["standard"]) for th in ths.values())
        # the best this script would ever accept: its floor price, any allowed values of the other terms
        options = [case["term_options"].get(f) or [None] for f in case["negotiable"][1:]]
        c_best = min(
            total_cost(
                case,
                {
                    **dict(zip(case["negotiable"][1:], combo, strict=True)),
                    "unit_price": round(th["opening"] * FLOOR, 2),
                },
            )
            for th in ths.values()
            for combo in itertools.product(*options)
        )
        alt = case["buyer"].get("alternative") or {}
        c_alt = total_cost(case, alt["terms"]) if alt.get("terms", {}).get("unit_price") else None
        r = {
            "ended": True,
            "deal": False,
            "supplier_id": None,
            "offer_id": None,
            "terms": None,
            "c_open": c_open,
            "c_deal": None,
            "c_alt": c_alt,
            "c_best": c_best,
            "score": score(c_open, c_alt, c_best) if c_alt else 0.0,
            "must_haves_ok": None,
            "cost_breakdown": None,
            "trace": [],
        }
        if sup:
            t = closing_terms(case, ths[sup]["stated"], ths[sup]["standard"])
            r.update(
                deal=True,
                supplier_id=sup,
                offer_id=ths[sup]["offer_id"],
                terms=t,
                c_deal=total_cost(case, t),
                must_haves_ok=must_haves_ok(case, t),
                cost_breakdown=cost_breakdown(case, t),
            )
            r["score"] = score(c_open, r["c_deal"], c_best) if r["must_haves_ok"] else 0.0
        s.update(status="ended", end_reason=reason, result=r)
        for th in ths.values():
            th["closed"] = th["closed"] or reason

    def message(self, team, sid, sup, msg):
        s = self.session(team, sid)
        if s["status"] != "open":
            raise Fail(409, f"the session has ended: {s['end_reason']}")
        if sup not in s["threads"]:
            raise Fail(422, f"{sup} is not a supplier in this case")
        th, action = s["threads"][sup], msg.get("action") or {}
        if action.get("type") == "walk_away":
            self._end(s, "walked_away", None)
            note = {"body": "Simulated supplier. Understood - no deal.", "term_sheets": [], "attachments": []}
            return {**th["messages"][-1], **note, "result": _public(s["result"])}
        if sup in s["late"]:
            raise Fail(409, f"{LATE}; GET the thread for the supplier's answer")
        if th["closed"]:
            raise Fail(409, f"the thread with {sup} has ended: {th['closed']}")
        if action.get("type") == "accept":
            sheet = action.get("term_sheet_id")
            owner = next((k for k, t in s["threads"].items() if t["offer_id"] == sheet and not t["closed"]), None)
            if owner is None:
                raise Fail(422, "not a standing offer in this session")
            reply = self._mail(
                s,
                s["threads"][owner],
                "agreed",
                s["threads"][owner]["stated"],
                "Thank you for your order.",
                ["order-confirmation.pdf"],
            )
            self._end(s, "accepted", owner)
            return {**reply, "result": _public(s["result"])}
        if action:
            raise Fail(422, "action is {'type': 'accept', 'term_sheet_id': ...} or 'walk_away'")
        sheets = [self._check(s["case"], ts.get("terms", {}), th) for ts in msg.get("term_sheets") or []]
        if len(sheets) > 3 or len(msg.get("body", "")) > 8000 or len(msg.get("refs", [])) > 20:
            raise Fail(422, "at most 3 term sheets, 8,000 characters and 20 refs")
        if th["round"] >= s["case"]["round_limit"]:
            raise Fail(409, "round limit reached: accept the standing offer or walk away")
        th.update(round=th["round"] + 1, timeouts=0, last_at=self.clock())
        th["messages"].append({**msg, "from": "buyer"})
        reply = self._answer(s, th, msg, sheets)
        self._settle(s, "no_deal")
        return {**reply, "result": _public(s["result"])} if s["result"] else reply

    def attachment(self, team, sid, att):
        s = self.session(team, sid)
        if att not in s["files"]:
            raise Fail(404, "no such attachment in this session")
        kind, stated, standard = s["files"][att]
        if kind.endswith(".xlsx"):
            return xlsx(stated["unit_price"]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        fixed = s["case"].get("fixed_terms", {})
        if kind.startswith("order"):
            return pdf("Order confirmation", [("Agreed terms", {**fixed, **standard, **stated})]), "application/pdf"
        rest = {k: v for k, v in standard.items() if k not in stated}
        sections = [
            ("Offered terms", stated),
            ("Our standard terms (apply to everything not stated above)", rest),
            ("Terms fixed by your enquiry", fixed),
        ]
        return pdf("Quote - simulated supplier", sections), "application/pdf"

    def state(self, team, sid):
        s, now = self.session(team, sid), self.clock()
        threads = {
            k: {
                "offer_id": t["offer_id"],
                "status": t["status"],
                "closed": t["closed"],
                "offers": t["round"] - t["timeouts"],
                "seconds_left": None
                if t["closed"] or self.no_clock
                else max(0.0, round(t["last_at"] + MOVE_S - now, 1)),
            }
            for k, t in s["threads"].items()
        }
        out = {
            "session_id": sid,
            "case_id": s["case"]["case_id"],
            "set": "practice",
            "status": s["status"],
            "end_reason": s["end_reason"],
            "threads": threads,
            "rounds": sum(t["round"] for t in s["threads"].values()),
            "opening": {k: structured(t["messages"][0], 0) for k, t in s["threads"].items()},
            "moves": s["moves"],
        }
        if s["result"]:
            out.update(result={**s["result"], "trace": s["trace"]}, replay_url=f"/replay/{sid}")
        return out

    def thread(self, team, sid, sup):
        th = self.session(team, sid)["threads"].get(sup)
        if th is None:
            raise Fail(404, f"{sup} is not a supplier in this case")
        return {
            "supplier_id": sup,
            "offer_id": th["offer_id"],
            "status": th["status"],
            "closed": th["closed"],
            "messages": th["messages"],
            "timeouts": th["late"],
        }

    def board(self):
        rows = []
        for team, name in self.teams.items():
            best, ended = {}, [s for s in self._mine(team) if s["result"]]
            for s in ended:
                best[s["case"]["case_id"]] = max(best.get(s["case"]["case_id"], 0), s["result"]["score"])
            total = round(100 * sum(best.values()) / len(self.cases), 1)
            deals, rounds = (
                sum(s["result"]["deal"] for s in ended),
                sum(self.state(team, s["id"])["rounds"] for s in ended),
            )
            rows.append({"team_name": name, "played": len(best), "total": total, "deals": deals, "rounds": rounds})
        rows.sort(key=lambda r: -r["total"])
        teams = [{**r, "rank": i} for i, r in enumerate(rows, 1)]
        return {"set": "practice", "cases": len(self.cases), "window": None, "teams": teams}


def _public(result):
    return {k: v for k, v in result.items() if k != "trace"}


PAGE_CSS = """body{font:15px/1.45 system-ui,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;
color:#1d1d1f;background:#fff}h1{font-size:20px}h2{font-size:17px;margin-top:28px}
table{border-collapse:collapse;margin:6px 0}caption{text-align:left}
td,th{border:1px solid #ccc;padding:3px 8px;text-align:left}
.mail{border:1px solid #ccc;border-radius:6px;padding:8px 12px;margin:10px 0}.buyer{background:#eef4ff}
.who{font-weight:600}.tag{color:#555;font-size:13px}.body{white-space:pre-wrap;margin:6px 0}
@media (prefers-color-scheme:dark){body{color:#e8e8e8;background:#18181a}.buyer{background:#1e2a3d}
.tag{color:#aaa}td,th,.mail{border-color:#444}}"""


def replay_html(replay):
    """The replay as a page for a browser: the result on top, then each thread's e-mails in order."""
    e, r = html.escape, replay["result"]

    def sheet(terms, caption=""):
        cap = f"<caption class='tag'>{e(caption)}</caption>" if caption else ""
        rows = "".join(f"<tr><td>{e(NAMES.get(k, k))}</td><td>{e(_fmt(v))}</td></tr>" for k, v in terms.items())
        return f"<table>{cap}{rows}</table>"

    money = {k: "-" if r[k] is None else f"{r[k]:,.2f}" for k in ("c_open", "c_deal", "c_best")}
    head = f"Deal with {r['supplier_id']}" if r["deal"] else "No deal"
    out = [
        f"<h1>Replay {e(replay['session_id'])} - {e(replay['case_id'])}</h1><h2>{e(head)}</h2><table>",
        *(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in money.items()),
        f"<tr><th>score</th><td>{r['score']:.3f}</td></tr></table>",
        sheet(r["terms"], f"agreed terms ({r['offer_id']})") if r["deal"] else "",
    ]
    for sup, messages in replay["threads"].items():
        out.append(f"<h2>Thread with {e(sup)}</h2>")
        for m in messages:
            buyer = m.get("from") == "buyer"
            status = "" if buyer else f" <span class='tag'>{e(str(m.get('status')))}</span>"
            out.append(f"<div class='mail {'buyer' if buyer else 'supplier'}'>")
            out.append(f"<div><span class='who'>{e(str(m.get('from')))}</span>{status}</div>")
            out.append(f"<div class='tag'>{e(str(m.get('subject', '')))}</div>")
            out.append(f"<div class='body'>{e(str(m.get('body', '')))}</div>")
            for t in m.get("term_sheets") or []:
                out.append(sheet(t.get("terms", {}), t.get("term_sheet_id") or "term sheet"))
            if m.get("action"):
                out.append(f"<div class='tag'>action: {e(json.dumps(m['action']))}</div>")
            if m.get("attachments"):
                out.append(f"<div class='tag'>attached: {e(', '.join(a['name'] for a in m['attachments']))}</div>")
            out.append("</div>")
    title = f"Replay {replay['session_id']}"
    return (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{e(title)}</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{PAGE_CSS}</style></head>"
        f"<body>{''.join(out)}</body></html>"
    )


def create_app(sim):
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    app = FastAPI(title="arena-sim")

    @app.middleware("http")
    async def size_limit(request: Request, call_next):
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return JSONResponse({"detail": "request body too large"}, 413)
        return await call_next(request)

    @app.exception_handler(Fail)
    async def failed(_, e: Fail):
        return JSONResponse({"detail": e.detail}, e.status, headers=e.headers)

    def team_of(request, post=False):
        key = request.headers.get("x-api-key", "")
        if key not in sim.teams:
            raise Fail(401, "missing or unknown X-API-Key")
        if post:
            now, calls = time.monotonic(), sim.calls.setdefault(key, [])
            calls[:] = [t for t in calls if now - t < 1]
            if len(calls) >= MAX_CALLS:
                raise Fail(429, f"more than {MAX_CALLS} calls per second", {"Retry-After": "1"})  # as the arena
            calls.append(now)
        return key

    def act(request, req, fn):
        """One POST: authenticated, rate-limited, answered from the store when retried."""
        with sim.lock:
            team, move_id = team_of(request, post=True), request.headers.get("x-move-id")
            digest = hashlib.sha256(json.dumps([request.url.path, req], sort_keys=True).encode()).hexdigest()
            if move_id and (team, move_id) in sim.replies:
                seen, reply = sim.replies[(team, move_id)]
                if seen != digest:
                    raise Fail(422, "X-Move-Id was already used for a different request")
                return reply
            reply = fn(team)
            if move_id:
                sim.replies[(team, move_id)] = (digest, reply)
            return reply

    def read(request, fn):
        with sim.lock:
            return fn(team_of(request))

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/v1/register")
    def register(body: dict):
        with sim.lock:
            if body.get("event_code") != sim.code:
                raise Fail(403, "wrong event code")
            if body.get("team_name") in sim.teams.values():
                raise Fail(409, "team name already registered")
            key = hashlib.sha256(f"{body.get('team_name')}{time.time()}".encode()).hexdigest()
            sim.teams[key] = body.get("team_name")
            return {"team_id": f"team-{key[:8]}", "api_key": key}

    @app.get("/v1/cases")
    def cases(request: Request, set: str = "practice"):
        if set == "final":
            raise Fail(403, "the final window is not open")
        return read(request, lambda t: [{**c, "hosted": True} for c in sim.cases.values()])

    @app.post("/v1/sessions")
    def create(request: Request, body: dict, response: Response):
        reply = act(request, body, lambda t: sim.open(t, body.get("case_id")))
        response.headers[REMAINING] = str(read(request, sim.practice)["left"])
        return reply

    @app.get("/v1/me")
    def me(request: Request):
        def fn(t):
            best = {}
            for x in sim._mine(t):
                if x["result"]:
                    best[x["case"]["case_id"]] = max(best.get(x["case"]["case_id"], 0), x["result"]["score"])
            return {"team_name": sim.teams[t], "status": "active", "practice": sim.practice(t), "best": best}

        return read(request, fn)

    @app.get("/v1/sessions")
    def sessions(request: Request, set: str = "practice"):
        keep = ("case_id", "status", "end_reason", "rounds")
        return read(
            request,
            lambda t: [
                {
                    **{k: sim.state(t, s["id"])[k] for k in keep},
                    "session_id": s["id"],
                    **{k: (s["result"] or {}).get(k) for k in ("deal", "score")},
                    "created_at": s["created_at"],
                }
                for s in sim._mine(t)
            ],
        )

    @app.get("/v1/sessions/{sid}")
    def state(request: Request, sid: str):
        return read(request, lambda t: sim.state(t, sid))

    @app.post("/v1/sessions/{sid}/threads/{sup}/messages")
    def message(request: Request, sid: str, sup: str, body: dict):
        return act(request, body, lambda t: sim.message(t, sid, sup, body.get("message") or {}))

    @app.get("/v1/sessions/{sid}/threads/{sup}")
    def thread(request: Request, sid: str, sup: str):
        return read(request, lambda t: sim.thread(t, sid, sup))

    @app.get("/v1/sessions/{sid}/attachments/{att}")
    def attachment(request: Request, sid: str, att: str):
        data, media = read(request, lambda t: sim.attachment(t, sid, att))
        return Response(data, media_type=media)

    @app.get("/v1/sessions/{sid}/backoffice")
    def backoffice(request: Request, sid: str):
        def fn(t):
            if sim.state(t, sid)["status"] != "ended":
                raise Fail(403, "the back office shows after the case ends")
            return [{"from": "sales", "to": "team", "text": "The simulator has no back office."}]

        return read(request, fn)

    @app.post("/v1/sessions/{sid}/moves")
    def move(request: Request, sid: str, body: dict):
        def fn(t):  # the structured path: an offer is an e-mail with one term sheet
            m = body.get("move") or {}
            if m.get("type") != "offer":
                raise Fail(422, "a move has type 'offer'; use /accept or /walk_away to close")
            refs = [r for c in m.get("claims", []) for r in c.get("refs", [])]
            msg = {"body": m.get("text", ""), "refs": refs, "term_sheets": [{"terms": m.get("terms", {})}]}
            reply = sim.message(t, sid, body.get("supplier_id", ""), msg)
            out = structured(reply, sim.sessions[sid]["threads"][body["supplier_id"]]["round"])
            sim.sessions[sid]["moves"].append({"supplier_id": body["supplier_id"], "move": m, "reply": out})
            return out

        return act(request, body, fn)

    @app.post("/v1/sessions/{sid}/accept")
    def accept(request: Request, sid: str, body: dict):
        def fn(t):
            owner = next(
                (k for k, th in sim.session(t, sid)["threads"].items() if th["offer_id"] == body.get("offer_id")), ""
            )
            msg = {"action": {"type": "accept", "term_sheet_id": body.get("offer_id")}}
            return sim.message(t, sid, owner, msg)["result"]

        return act(request, body, fn)

    @app.post("/v1/sessions/{sid}/walk_away")
    def walk_away(request: Request, sid: str):
        def fn(t):
            sup = next(iter(sim.session(t, sid)["threads"]))
            return sim.message(t, sid, sup, {"action": {"type": "walk_away"}})["result"]

        return act(request, {}, fn)

    @app.get("/v1/leaderboard")
    def leaderboard(set: str = "practice"):
        with sim.lock:
            return sim.board()

    @app.get("/leaderboard", response_class=HTMLResponse)
    def leaderboard_html():
        with sim.lock:
            teams = sim.board()["teams"]
        rows = "".join(
            f"<tr><td>{t['rank']}</td><td>{html.escape(t['team_name'])}</td><td>{t['total']}</td></tr>" for t in teams
        )
        return f"<h1>arena-sim</h1><table>{rows}</table>"

    @app.get("/replay/{sid}")
    def replay(request: Request, sid: str):
        with sim.lock:
            s = sim.sessions.get(sid)
            if s is None or s["status"] != "ended":
                raise Fail(404, "no replay yet: the session is open")
            threads = {k: th["messages"] for k, th in s["threads"].items()}
            out = {"session_id": sid, "case_id": s["case"]["case_id"], "result": s["result"], "threads": threads}
            if "text/html" in request.headers.get("accept", ""):  # a browser: the same replay as a page
                return HTMLResponse(replay_html(out))
            return out

    return app


def load_cases(folder=CASES):
    found = (json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(folder).glob("*.json")))
    return {c["case_id"]: c for c in found}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="arena-sim", description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["serve"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--data", help="data package folder: cited IDs are checked against it")
    ap.add_argument("--event-code", default="sim")
    ap.add_argument("--no-clock", action="store_true", help="no 60-second rule: threads wait forever (by hand)")
    a = ap.parse_args(argv)
    facts = {}
    if a.data:
        from .data import DataPackage

        facts = DataPackage(a.data).facts
    import uvicorn

    if a.no_clock:
        print("arena-sim: --no-clock - no 60-second rule, threads wait forever (the arena keeps the rule)", flush=True)
    else:
        print("arena-sim: the 60-second rule is on, as on the arena (--no-clock switches it off)", flush=True)
    app = create_app(Sim(load_cases(), facts, a.event_code, no_clock=a.no_clock))
    uvicorn.run(app, host=a.host, port=a.port)
