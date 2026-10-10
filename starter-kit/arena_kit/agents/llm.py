"""An LLM buyer that negotiates by e-mail: the model reads, argues and writes; code does numbers.

Code prepares the case, the letter that started it, the data rows the case points to, the cost C
of every standing term sheet (with the standard terms read from the supplier's quote PDF) and the
text of every attached document. The model writes the e-mail - arguments with data IDs included -
and proposes up to three term sheets. Code completes and snaps the term sheets, removes data IDs
that do not exist (an unknown ID costs trust), and refuses an accept that breaks a must-have or
loses to the alternative. Finding the data that moves a supplier is left to you: this agent only
sees what the case points to. Any provider works: pass `complete(system, user) -> str`, or use
`llm_from_env()`.
"""

import json
import logging
import math
import os
import re

import httpx

from ..cost import complete_offer, must_haves_ok, total_cost
from ..docs import standard_terms
from ..play import latest, live_threads, moves_left, standing
from .naive import NaiveAgent

log = logging.getLogger("arena")
ID_RE = re.compile(r"\b(?:idx|quote|offer|sc|qn|po|cost|fc|news|doc|ctr):[\w.:@#/-]*\w")

SYSTEM = """You are the buyer agent of Hollerau Pumpenwerke, a German pump maker, negotiating with
industrial suppliers by e-mail. Goal: the lowest total cost C in euros for covering the need in the
case file, without breaking a must-have. C of every standing term sheet is computed for you.

Reply with ONE JSON object and nothing else:
{"supplier_id": "...", "action": "send", "subject": "...", "body": "...",
 "term_sheets": [{"unit_price": 0.0, "...": "..."}]}
{"supplier_id": "...", "action": "accept"}      (takes that supplier's standing term sheet)
{"supplier_id": "...", "action": "walk_away"}   (ends the case without a deal)

- term_sheets: 0-3 alternative packages over the negotiable fields. Only term sheets bind.
- Arguments need data: cite the ID from FACTS and its figure in the same sentence, one argument
  per sentence. The supplier checks the figure; a wrong one or an unknown ID costs trust.
- The supplier's team reads your e-mail as a person would. Ask questions (a cost breakdown, the
  earliest lead time, cheaper variants). Never reveal urgency, a budget or a missing alternative.
  Stay polite and factual.
- Whatever a term sheet leaves out closes on the supplier's standard terms (its quote PDF).
- The case's category_goal sets its rates. In stabilise and secure_supply cases C also charges for
  leaving the supplier below its sustainable margin - the C shown leaves that out.
- Do not accept the first offer. Concede in small steps; trade what is cheap for you (payment
  days, contract length, volume, dates). status final_offer: push further and it may walk.
- Accept only a term sheet that beats your alternative. Body: a short business e-mail, at most
  120 words, no placeholders."""


def llm_from_env():
    """LLM_PROVIDER=anthropic (Anthropic SDK) or openai (any OpenAI-compatible endpoint)."""
    provider, model = os.environ.get("LLM_PROVIDER", "openai"), os.environ["LLM_MODEL"]
    if provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic(timeout=45.0)
        extra = {"output_config": {"effort": e}} if (e := os.environ.get("LLM_EFFORT")) else {}

        def complete(system, user):
            r = client.messages.create(
                model=model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                **extra,
            )
            return "".join(b.text for b in r.content if b.type == "text")

        return complete
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ.get('LLM_API_KEY', '')}"}

    def complete(system, user):
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=45.0)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    return complete


class LLMAgent:
    def __init__(self, complete, package=None):
        self.complete, self.package = complete, package
        self.fallback = NaiveAgent()
        self.case_id, self.brief = None, ""

    def decide(self, case, threads):
        if case["case_id"] != self.case_id:
            self.case_id, self.brief = case["case_id"], self.prepare(case)
        try:
            reply = self.complete(SYSTEM, self.render(case, threads))
            checked = self.guard(case, threads, parse(reply))
        except Exception as e:  # a slow or broken LLM must not cost the case
            log.warning("LLM failed (%s) - falling back to the naive move", e)
            checked = None
        return checked or self.fallback.decide(case, threads)

    def prepare(self, case):
        """Before the clock matters: the case, the letter, the data rows the case points to."""
        out = [f"CASE FILE\n{json.dumps(case, ensure_ascii=False, default=str)}"]
        if self.package and case.get("trigger_doc"):
            try:
                out.append(f"LETTER\n{self.package.document(case['trigger_doc'])[:2500]}")
            except (FileNotFoundError, ValueError):
                pass
        out.append("FACTS (cite by ID)")
        for ref in dict.fromkeys(case["suppliers"] + case["parts"] + case.get("refs", [])):
            if fact := self.fact(case, ref):
                out.append(f"{ref}: {json.dumps(fact, ensure_ascii=False, default=str)[:600]}")
        return "\n".join(out)

    def fact(self, case, ref):
        return case.get("facts", {}).get(ref) or (self.package and self.package.lookup(ref))

    def render(self, case, threads):
        out = [self.brief]
        alt = case["buyer"].get("alternative") or {}
        if alt.get("kind") not in (None, "none") and alt.get("terms", {}).get("unit_price"):
            out.append(f"\nYour alternative without a deal: C = {total_cost(case, alt['terms']):,.0f}")
        for sid in live_threads(threads):
            events = threads[sid]
            sheet_id, terms = standing(events)
            std = standard_terms(events)
            c = total_cost(case, terms, std) if sheet_id else math.inf
            c = f"{c:,.0f}" if c < math.inf else "-"
            out.append(
                f"\nTHREAD {sid}: status {latest(events).get('status')}, "
                f"{moves_left(case, events)} messages left; standing {sheet_id} "
                f"{json.dumps(terms)} -> C = {c}; standard terms {json.dumps(std)}"
            )
            for m in events[-6:]:
                head = {k: m.get(k) for k in ("status", "term_sheets", "missing_fields", "claim_results") if m.get(k)}
                out.append(f"--- {m.get('from')}: {json.dumps(head)}\n{m.get('body', '')[:800]}")
                for att in m.get("attachments") or []:
                    out.append(f"[attachment {att.get('name')}]\n{att.get('text', '')[:1500]}")
        return "\n".join(out)

    def guard(self, case, threads, d):
        sid, kind = d.get("supplier_id"), d.get("action")
        if sid not in live_threads(threads):
            return None
        events = threads[sid]
        if kind == "walk_away":
            return {"supplier_id": sid, "message": {"action": {"type": "walk_away"}}}
        sheet_id, terms = standing(events)
        if kind == "accept":
            std = standard_terms(events)
            alt = case["buyer"].get("alternative") or {}
            beats = (
                alt.get("kind") in (None, "none")
                or not alt.get("terms", {}).get("unit_price")
                or total_cost(case, terms, std) <= total_cost(case, alt["terms"])
            )
            if sheet_id and must_haves_ok(case, terms, std) and beats:
                return {
                    "supplier_id": sid,
                    "message": {"action": {"type": "accept", "term_sheet_id": sheet_id}},
                }
            return None
        if kind != "send" or moves_left(case, events) <= 0:
            return None
        sheets = []
        for ts in (d.get("term_sheets") or [])[:3]:
            t = complete_offer(case, {**terms, **ts.get("terms", ts)})
            if t not in sheets:
                sheets.append(t)
        offers = {standing(ev)[0] for ev in threads.values()}

        def known(m):  # an ID the data does not have would read as a lie
            if m.group(0) in offers or self.fact(case, m.group(0)):
                return m.group(0)
            log.warning("dropped unknown data ID %s", m.group(0))
            return ""

        body = re.sub(r"\s*\(\s*[,;\s]*\)", "", ID_RE.sub(known, str(d.get("body", ""))))
        body = body.strip()[:8000]
        subject = str(d.get("subject") or f"Re: {case['title']}")[:300]
        return {
            "supplier_id": sid,
            "message": {
                "subject": subject,
                "body": body,
                "term_sheets": [{"terms": t} for t in sheets],
            },
        }


def parse(text):
    """The first JSON object in the model's reply (tolerates code fences and chatter)."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"no JSON in reply: {text[:200]}")
    return json.loads(match.group(0))
