"""Prompts for the Negotiator, Strategist, Writer and Reflector, and their output checks."""

import json

from .priors import GENERAL

PROMPT_LIMIT = 60_000

ROLE = (
    "You are the lead buyer of Hollerau Pumpenwerke GmbH, a German pump maker, negotiating by e-mail with "
    "industrial suppliers. Each supplier is a person with its own economics: read what moves it (its notes, "
    "history, news, quote remarks, latest e-mail) and find the trade it wants."
)

NUMBERS = (
    "Code has computed every number. MENU: valid term-sheet packages per supplier (mid, terms, c = our total "
    "cost in EUR, lower is better for us; saving = how much cheaper than its standing offer). Code sizes our "
    "move each round: 'step' packages all cost us about the same and differ only in what the supplier gets "
    "(payment, contract, index clause, quality terms) - offer the 2-3 that fit what this supplier values; "
    "'trade' (only after a final offer) is worth exactly as much to the supplier as its own offer but cheaper "
    "for us. Send 1-3 packages from ONE level as alternatives; every e-mail carries packages. CLAIMS: verified "
    "data arguments (cid, sentence); "
    "pick at most 3, only ones that fit this supplier. LESSONS: what earlier cases of this run taught us - follow "
    "them as uncertain hypotheses, never as causal facts. Supplier preferences are separate from cash value. "
    "Respect evidenced counterconditions and do not repeat declined or answered questions. "
    "You may add signals: [{field, values: [allowed values], evidence: verbatim span from the latest email}] "
    "and proposal: {non-price negotiable field: allowed value}. Code computes the proposal's price. "
    "Signals restrict future packages: use them only for explicit counterconditions or preferences. "
    "Acceptance of our proposal does not establish a requirement: 'agreeable to fourteen days' does not "
    "mean it refuses longer payment. Do not turn our own concessions into supplier demands. "
    "A signal describes this supplier only; use an empty list when uncertain."
)

WRITING = (
    "Write plain, professional, concise e-mails (at most about 150 words) as the purchasing team. NEVER write "
    "digits, prices, percentages, dates or data IDs yourself: put {{CLAIM:Cn}} where a claim sentence goes, "
    "{{SHEETS}} where the packages go and {{QUESTIONS}} for your questions. Each placeholder stands on its own "
    "line, as a whole sentence or block - never inside a sentence of yours. Ask each question once: either in "
    "your text or in questions, not both. Address the person who signed the supplier's latest e-mail by name. "
    "Never mention urgency, budgets, deadlines, targets or whether we have alternatives. No threats. Close with "
    "'Best regards' and 'Hollerau Pumpenwerke GmbH, Purchasing'."
)

NEGOTIATOR_OUT = (
    'Reply with ONE JSON object and nothing else: {"suppliers": {"<supplier_id>": {"read": "<what moves it, '
    '<=200 chars>", "tactic": "anchor|trade|probe|concede|close_push", "level": "step|trade", '
    '"sheet_ids": ["M1"], "claim_ids": ["C1"], "ask_breakdown": true, "questions": ["<=140 chars, no digits"], '
    '"tone": "warm|neutral|firm", "prediction": {"status": "open|final_offer|agreed|walked_away", '
    '"price_move_pct": 1.5, "note": "<=140 chars"}, "signals": [], "proposal": {}, '
    '"email": {"subject": "...", "body": "..."}}}}. '
    "Include every supplier listed under suppliers."
)

STRATEGIST_OUT = NEGOTIATOR_OUT.replace(', "email": {"subject": "...", "body": "..."}', "") + (
    ' Also add "plan": "<your plan for the next rounds, <=300 chars>" per supplier.'
)

WRITER_OUT = (
    'Reply with ONE JSON object and nothing else: {"emails": {"<supplier_id>": {"subject": "...", "body": "..."}}}.'
)

REFLECTOR = (
    "You review a negotiation our buyer agent just closed, to teach the next cases of this run. Compare what "
    "we predicted with what the supplier did; note what was too aggressive or too soft, which arguments and "
    "levers coincided with changes, and any mistake (contradicted data, wasted rounds, ignored signals). "
    "Treat proposed explanations as hypotheses. Price moves and final scores do not establish causation. "
    'Reply with ONE JSON object: {"lessons": [{"scope_level": "global|case_type|goal|archetype|supplier", '
    '"scope_value": "<case type, goal, archetype or observed supplier ID; empty for global>", "polarity": "do|avoid", '
    '"lever": "price|payment_days|contract_months|incoterm|volume|claims|tone|closing|timing|other", '
    '"lesson": "<one actionable sentence, <=200 chars>"}], "supplier_reads": {"<supplier_id>": "<=200 chars"}, '
    '"feedback": {"<key of each lesson in lessons_used>": "held|failed"}}. '
    "Give 1-4 lessons that would transfer to OTHER suppliers of the same kind, not facts about this case only."
)


def _fit(payload):
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text.encode()) <= PROMPT_LIMIT:
        return text
    for sup in payload.get("suppliers", {}).values():  # trim the bulkiest parts first
        sup.get("ledger", {}).pop("attachments", None)
        if "dossier" in sup:
            sup["dossier"]["notes"] = sup["dossier"].get("notes", [])[:3]
            sup["dossier"].pop("history", None)
    payload.pop("trigger_letter", None)
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return text[: PROMPT_LIMIT * 2 // 3] if len(text.encode()) > PROMPT_LIMIT else text


def negotiator(payload, write=True):
    system = "\n\n".join(
        [
            ROLE,
            NUMBERS,
            *([WRITING] if write else []),
            "Playbook:\n- " + "\n- ".join(GENERAL),
            NEGOTIATOR_OUT if write else STRATEGIST_OUT,
        ]
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": _fit(payload)}]


def writer(payload):
    system = "\n\n".join([ROLE, WRITING, WRITER_OUT])
    return [{"role": "system", "content": system}, {"role": "user", "content": _fit(payload)}]


def reflector(payload):
    return [{"role": "system", "content": REFLECTOR}, {"role": "user", "content": _fit(payload)}]


def _ids(value, key):
    """A list of ID strings from whatever the model returned: "M1", ["M1"], [{"mid": "M1"}], [["M1"]]."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple):
        return []
    out = []
    for item in value:
        if isinstance(item, dict):
            item = item.get(key)
        elif isinstance(item, list | tuple) and item:
            item = item[0]
        if isinstance(item, str) and item.strip() not in out:
            out.append(item.strip())
    return out


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ""


def clean_choice(raw, menu, claims, fallback):
    """Validate one supplier's model choice against the menu/claims; fill gaps from `fallback`.
    Total: any JSON value is accepted and anything unusable falls back."""
    if not isinstance(raw, dict):
        return dict(fallback, source="det")
    mids = {m["mid"]: m for m in menu}
    sheet_ids = [s for s in _ids(raw.get("sheet_ids"), "mid") if s in mids][:3]
    if sheet_ids:  # keep one level only: the first sheet's
        level = mids[sheet_ids[0]]["level"]
        sheet_ids = [s for s in sheet_ids if mids[s]["level"] == level]
    cids = {c["cid"] for c in claims}
    claim_ids = [c for c in _ids(raw.get("claim_ids"), "cid") if c in cids][:3]
    qs = raw.get("questions")
    qs = [qs] if isinstance(qs, str) else (qs if isinstance(qs, list) else [])
    questions = [q.strip()[:140] for q in qs if isinstance(q, str) and len(q.strip()) >= 10][:2]
    pred = raw.get("prediction") if isinstance(raw.get("prediction"), dict) else {}
    email = raw.get("email") if isinstance(raw.get("email"), dict) else {}
    move = pred.get("price_move_pct")
    return {
        "tactic": _text(raw.get("tactic"), 20) or fallback["tactic"],
        "sheet_ids": sheet_ids or fallback["sheet_ids"],
        "claim_ids": claim_ids,
        "ask_breakdown": raw.get("ask_breakdown") is True,
        "tone": raw.get("tone") if raw.get("tone") in ("warm", "neutral", "firm") else fallback["tone"],
        "questions": questions,
        "prediction": {
            "status": _text(pred.get("status"), 20) or None,
            "price_move_pct": move if isinstance(move, int | float) and not isinstance(move, bool) else None,
            "note": _text(pred.get("note"), 140),
        },
        "read": _text(raw.get("read"), 200),
        "plan": _text(raw.get("plan"), 300),
        "subject": _text(email.get("subject"), 200),
        "body": _text(email.get("body"), 3000),
        "source": "llm",
    }


def supplier_entries(raw, suppliers):
    """{sid: raw choice} from a model reply, tolerating keys like "70340" or a list of entries."""
    block = raw.get("suppliers") if isinstance(raw, dict) else None
    if isinstance(block, list):
        block = {str(e.get("supplier_id")): e for e in block if isinstance(e, dict)}
    if not isinstance(block, dict):
        return {}
    out = {}
    for sid in suppliers:
        num = sid.split(":")[-1]
        for key in (sid, num, f"sup{num}"):
            if isinstance(block.get(key), dict):
                out[sid] = block[key]
                break
    return out


def clean_lessons(raw):
    out = []
    items = raw.get("lessons") if isinstance(raw, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("lesson"), str) or not item["lesson"].strip():
            continue
        level = (
            item.get("scope_level")
            if item.get("scope_level") in ("global", "case_type", "goal", "archetype", "supplier")
            else "global"
        )
        # the model sometimes lists several archetypes in one value: one lesson per scope value
        values = [v.strip() for v in _text(item.get("scope_value"), 120).split(",") if v.strip()] or [""]
        for value in values if level != "global" else [""]:
            out.append(
                {
                    "scope": {"level": level, "value": value[:40]},
                    "polarity": "avoid" if item.get("polarity") == "avoid" else "do",
                    "lever": _text(item.get("lever"), 20) or "other",
                    "text": item["lesson"].strip()[:200],
                }
            )
    return out[:8]
