"""Dossier Builder: everything the data package says about the case's suppliers and parts.

Deterministic SQL plus keyword tagging of notes, letters, news and quote remarks (see `priors`).
Reads the Sunday release (CSV rows, not in the SQLite) when it is present.
"""

import csv
import re
from pathlib import Path

from .priors import ARCHETYPES

NOTE_CHARS = 220
TRIGGER_CHARS = 1500


def _rows(pkg, sql, params=(), limit=200):
    try:
        return pkg.query(sql, params, limit=limit)
    except Exception:
        return []


def _one(pkg, sql, params=()):
    rows = _rows(pkg, sql, params, limit=1)
    return rows[0] if rows else {}


def _read(base, rel):
    try:
        path = (Path(base) / rel).resolve()
        if path.is_relative_to(Path(base).resolve()) and path.is_file():
            return path.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def _release_rows(pkg, table):
    if not getattr(pkg, "release", None):
        return []
    path = Path(pkg.release) / "data" / f"{table}.csv"
    if not path.is_file():
        return []
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _sentence(text, phrase):
    """The sentence around a matched phrase, for evidence in prompts."""
    low = text.lower()
    i = low.find(phrase)
    if i < 0:
        return ""
    start = max(low.rfind(".", 0, i), low.rfind("\n", 0, i)) + 1
    end = min([j for j in (low.find(".", i), low.find("\n", i)) if j >= 0] or [len(text)])
    return re.sub(r"\s+", " ", text[start : end + 1]).strip()[:NOTE_CHARS]


def tag_text(text):
    """[(tag, phrase, sentence)] for every archetype phrase found in `text`."""
    low = (text or "").lower()
    hits = []
    for tag, spec in ARCHETYPES.items():
        for phrase in spec["phrases"]:
            if phrase in low:
                hits.append((tag, phrase, _sentence(text, phrase)))
                break
    return hits


def supplier_dossier(pkg, sid, parts):
    d = {"supplier_id": sid, "tags": [], "evidence": {}, "notes": []}

    def tag(name, ref, note=None):
        if name not in d["tags"]:
            d["tags"].append(name)
        d["evidence"].setdefault(name, [])
        if ref and ref not in d["evidence"][name]:
            d["evidence"][name].append(ref)
        if note and len(d["notes"]) < 8 and note not in [n["text"] for n in d["notes"]]:
            d["notes"].append({"ref": ref, "tag": name, "text": note})

    master = _one(pkg, "SELECT * FROM suppliers WHERE id=?", (sid,))
    d["name"] = master.get("name") or sid
    d["country"] = master.get("country")
    d["category"] = master.get("category")
    d["contract_id"] = master.get("contract_id")
    intel = _one(pkg, "SELECT * FROM supplier_intel WHERE supplier_id=?", (sid,))
    d["intel"] = {
        k: intel.get(k)
        for k in ("owner_type", "group_parent", "staff", "turnover_band", "equity_ratio_pct", "credit_index")
        if intel.get(k) not in (None, "")
    }
    if intel.get("credit_index") is not None and intel.get("credit_index_12m_ago") is not None:
        d["intel"]["credit_index_change_12m"] = intel["credit_index"] - intel["credit_index_12m_ago"]
        if d["intel"]["credit_index_change_12m"] >= 40:  # a higher credit index is worse
            tag("cash_short", sid, "Credit index worsened markedly in 12 months.")
    if intel.get("owner_type") in ("family", "founder"):
        d["intel"]["family_or_founder"] = True

    hist = _rows(
        pkg,
        "SELECT date, part_id, type, asked_pct, settled_pct, note FROM negotiation_history "
        "WHERE supplier_id=? ORDER BY date DESC",
        (sid,),
        limit=8,
    )
    d["history"] = hist[:6]
    settled = [h["settled_pct"] for h in hist if h.get("settled_pct") is not None]
    asked = [h["asked_pct"] for h in hist if h.get("asked_pct") is not None]
    d["history_avg"] = {
        "asked_pct": round(sum(asked) / len(asked), 2) if asked else None,
        "settled_pct": round(sum(settled) / len(settled), 2) if settled else None,
    }

    sc = _one(
        pkg,
        "SELECT id, quarter, otd_pct, ppm, colour, score FROM scorecards WHERE supplier_id=? ORDER BY quarter DESC",
        (sid,),
    )
    d["scorecard"] = sc
    late = _one(
        pkg,
        "SELECT AVG(julianday(paid_date)-julianday(due_date)) AS late FROM invoices "
        "WHERE supplier_id=? AND paid_date IS NOT NULL",
        (sid,),
    )
    d["late_payment_days"] = round(late["late"], 1) if late.get("late") is not None else None

    news = _rows(
        pkg,
        "SELECT id, date, topic, headline FROM news WHERE supplier_id=? AND topic NOT IN ('noise','market') "
        "ORDER BY date DESC",
        (sid,),
        limit=10,
    )
    news += [
        {k: r.get(k) for k in ("id", "date", "topic", "headline")}
        for r in _release_rows(pkg, "news")
        if r.get("supplier_id") == sid and r.get("topic") not in ("noise", "market")
    ]
    d["news"] = news
    for n in news:
        for name, spec in ARCHETYPES.items():
            if n.get("topic") in spec["news"]:
                tag(name, n.get("id"), n.get("headline"))

    docs = _rows(
        pkg,
        "SELECT id, date, kind, subject, file FROM documents WHERE supplier_id=? AND id NOT LIKE '%-N' "
        "ORDER BY date DESC",
        (sid,),
        limit=40,
    )
    for doc in docs:
        doc["base"] = pkg.root
    for r in _release_rows(pkg, "documents"):
        if r.get("supplier_id") == sid and not str(r.get("id", "")).endswith("-N"):
            docs.append({**{k: r.get(k) for k in ("id", "date", "kind", "subject", "file")}, "base": pkg.release})
    d["documents"] = [{k: doc.get(k) for k in ("id", "date", "kind", "subject")} for doc in docs[:12]]
    for doc in docs:
        text = _read(doc["base"], doc.get("file") or "")
        for name, _, sentence in tag_text(text):
            tag(name, doc["id"], sentence)

    quotes = _rows(
        pkg,
        "SELECT id, part_id, quote_date, unit_price, price_unit, payment_days, lead_time_days, incoterm, "
        "remarks FROM quotes WHERE supplier_id=? ORDER BY quote_date DESC",
        (sid,),
        limit=10,
    )
    d["quotes"] = [q for q in quotes if q.get("part_id") in parts][:4] or quotes[:2]
    for q in quotes:
        for name, _, sentence in tag_text(q.get("remarks") or ""):
            tag(name, q["id"], sentence)
    if (d["late_payment_days"] or 0) >= 7:
        tag("unpaid", None, f"We paid its invoices {d['late_payment_days']} days late on average.")
    return d


def part_dossier(pkg, part):
    p = _one(pkg, "SELECT id, description, price_unit, standard_price, material_group FROM parts WHERE id=?", (part,))
    p["cost_drivers"] = _rows(pkg, "SELECT series_id, weight FROM cost_drivers WHERE part_id=?", (part,))
    p["should_cost"] = _rows(
        pkg,
        "SELECT id, supplier_id, unit_cost, price_unit, confidence_pct FROM should_cost WHERE part_id=?",
        (part,),
    )
    return p


def trigger(pkg, case):
    ref = case.get("trigger_doc")
    if not ref:
        return {}
    fact = (case.get("facts") or {}).get(ref) or pkg.facts.get(ref) or {}  # a hidden case may bring its own letter
    out = {"id": ref, **{k: fact.get(k) for k in ("asked_pct", "asked_price", "days", "effective_date", "title")}}
    row = _one(pkg, "SELECT file FROM documents WHERE id=?", (ref,))
    text = _read(pkg.root, row.get("file") or "")
    if not text:
        for r in _release_rows(pkg, "documents"):
            if r.get("id") == ref:
                text = _read(pkg.release, r.get("file") or "")
    if not text:
        text = " ".join(str(fact.get(k)) for k in ("title", "summary", "text") if fact.get(k)) or case.get(
            "briefing", ""
        )
    out["text"] = text[:TRIGGER_CHARS]
    return out


def build(case, pkg):
    parts = case.get("parts") or []
    return {
        "suppliers": {sid: supplier_dossier(pkg, sid, parts) for sid in case.get("suppliers") or []},
        "parts": {p: part_dossier(pkg, p) for p in parts},
        "trigger": trigger(pkg, case),
    }


def digest(dossier, sid):
    """A compact view of one supplier for the prompt."""
    d = dossier["suppliers"].get(sid) or {}
    return {
        "name": d.get("name"),
        "country": d.get("country"),
        "tags": d.get("tags", []),
        "notes": d.get("notes", [])[:6],
        "intel": d.get("intel", {}),
        "history_avg": d.get("history_avg"),
        "history": [
            {k: h.get(k) for k in ("date", "type", "asked_pct", "settled_pct", "note")}
            for h in d.get("history", [])[:4]
        ],
        "scorecard": d.get("scorecard"),
        "news": [{k: n.get(k) for k in ("date", "topic", "headline")} for n in d.get("news", [])[:4]],
        "quote_remarks": [q.get("remarks") for q in d.get("quotes", []) if q.get("remarks")][:3],
        "late_payment_days": d.get("late_payment_days"),
    }
