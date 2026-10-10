"""Critic: turns a chosen move into a safe e-mail. Figures come from code; the model's prose is linted.

Placeholders the model may use: {{CLAIM:C1}} (the claim's sentence), {{SHEETS}} (one line per term
sheet), {{QUESTIONS}}. Model sentences with digits, currency, % or data IDs are dropped, and so are
sentences that leak urgency, a budget, a missing alternative or a target.
"""

import re

from .claims import ID_RE, MAX_PER_MESSAGE
from .offers import describe

PLACEHOLDER = re.compile(r"\{\{\s*(CLAIM:C\d+|SHEETS|QUESTIONS)\s*\}\}")
# near-misses the model writes: normalised before linting; claim blocks vanish (compose appends claims)
NEAR_MISS = [
    (re.compile(r"\{\{\s*(?:sheets?|terms|packages?|options?|proposals?)\s*\}\}", re.I), "{{SHEETS}}"),
    (re.compile(r"\{\{\s*questions?\s*\}\}", re.I), "{{QUESTIONS}}"),
    (re.compile(r"\{\{\s*claim\s*[:_ ]\s*(c\d+)\s*\}\}", re.I), lambda m: "{{CLAIM:" + m.group(1).upper() + "}}"),
    (re.compile(r"\{\{\s*claims?(?:_block)?\s*\}\}", re.I), ""),
]
LEAKS = re.compile(
    r"\b(urgent|urgently|urgency|asap|as soon as possible|(?:as )?(?:quickly|fast|soon) as possible|immediately|"
    r"deadline|budget|no alternatives?|no other (?:option|choice|supplier|source|vendor)s?|"
    r"(?:only|sole|single|exclusive) (?:supplier|source|vendor)|single source|"
    r"(?:cannot|can't|unable to) (?:source|buy|get|find) (?:this|it|them|these)? ?elsewhere|"
    r"(?:rely|relies|relying|depend|depends|dependent|depending) (?:on|upon) (?:you|your)|"
    r"(?:time|stock|inventory|supply) (?:is |are )?(?:short|low|tight|running (?:out|low))|"
    r"we need (?:this|it|them) (?:soon|quickly|fast|now)|must have|desperate|target price|walk away|"
    r"our limit|maximum we can|running out|production stop|line stop|critical shortage)\b",
    re.IGNORECASE,
)
FIGURE = re.compile(r"\d|€|%|\beur\b", re.IGNORECASE)
# figures spelled out: the model must not state prices or percentages in words either
NUMBER_WORDS = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
    r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|"
    r"million|half|quarter|double|twice|percent|per ?cent|percentage|euros?|cents?)\b",
    re.IGNORECASE,
)
BODY_LIMIT = 7000
SUBJECT_LIMIT = 200
LETTERS = "ABC"


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+|\n", text or "") if s.strip()]


def normalise(text):
    for pattern, repl in NEAR_MISS:
        text = pattern.sub(repl, text)
    return text


def lint(text):
    """Model prose without figures, IDs, leaks or broken placeholders; placeholders survive.
    Returns (text, issues)."""
    issues, kept = [], []
    for line in normalise(text or "").splitlines():
        out = []
        for sentence in _sentences(line):
            bare = PLACEHOLDER.sub("", sentence)
            if LEAKS.search(bare):
                issues.append("leak")
                continue
            if FIGURE.search(bare) or NUMBER_WORDS.search(bare) or ID_RE.search(bare):
                issues.append("figure")
                continue
            if "{{" in bare or "}}" in bare or "{" in bare or "}" in bare:
                issues.append("placeholder")
                continue
            out.append(sentence.strip())
        kept.append(" ".join(out))
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, issues


def sheet_lines(case, sheets, inline=False):
    """One line per sheet. `inline`: the text before it already introduces the packages (ends with ':')."""
    if not sheets:
        return ""
    if len(sheets) == 1:
        return ("" if inline else "Our proposal: ") + describe(case, sheets[0]) + "."
    lines = [] if inline else ["We could agree on any of these packages:"]
    lines += [f"Option {LETTERS[i]}: {describe(case, s)}." for i, s in enumerate(sheets)]
    return "\n".join(lines)


CLAIM_MARK = re.compile(r"\{\{CLAIM:C\d+\}\}")
SHEETS_MARK = "{{SHEETS}}"


def _isolate(body):
    """Placeholders stand for whole sentences or blocks. The model sometimes wraps them in its own words
    ("Our recent orders were at {{CLAIM:C1}}", "please consider the alternatives in {{SHEETS}}."), which
    doubled the claim's opening or cut a sentence in half: a claim replaces its whole sentence, and the
    packages get their own lines after the last complete sentence before them."""
    out = []
    for line in (body or "").splitlines():
        if CLAIM_MARK.search(line):
            line = " ".join(
                " ".join(CLAIM_MARK.findall(s)) if CLAIM_MARK.search(s) else s.strip() for s in _sentences(line)
            )
        if SHEETS_MARK in line:
            before, after = line.split(SHEETS_MARK, 1)
            before = before.rstrip()
            if before and not before.endswith(":"):
                # keep complete sentences, drop a fragment that led into the placeholder
                cut = max(before.rfind(". "), before.rfind("? "), before.rfind("! "))
                before = before[: cut + 1].rstrip() if cut >= 0 else ""
            after = after.lstrip(" .,;:")
            after = after.replace(SHEETS_MARK, "")
            out += [*([before, ""] if before else []), SHEETS_MARK, *(["", after] if after else [])]
            continue
        out.append(line)
    return "\n".join(out)


def _words(text):
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2}


def _similar(a, b, bar=0.6):
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= bar


def _dedupe_questions(text):
    """Drop a question that repeats an earlier one in other words (the model often asks in the body and
    again in its question list)."""
    asked, lines = [], []
    for line in text.splitlines():
        kept = []
        for s in _sentences(line):
            if s.strip().endswith("?"):
                if any(_similar(s, q) for q in asked):
                    continue
                asked.append(s)
            kept.append(s.strip())
        if kept or not line.strip():
            lines.append(" ".join(kept))
    return "\n".join(lines)


def compose(case, body, claims, sheets, questions, notes=()):
    """Fill placeholders; append anything the model forgot. `notes`: code-written sentences (answers to the
    supplier's questions). Returns (body, refs, cids in body order)."""
    by_id = {c["cid"]: c for c in claims}
    used = []
    body = _isolate(body)

    def fill(match):
        key = match.group(1)
        if key == "SHEETS":
            start = match.start()
            line_start = body.rfind("\n", 0, start) + 1
            before = body[line_start:start].strip() or body[: max(0, line_start - 1)].rstrip()[-1:]
            return sheet_lines(case, sheets, inline=before.endswith(":"))
        if key == "QUESTIONS":
            return " ".join(q.strip() for q in questions if q.strip())
        cid = key.split(":", 1)[1]
        if cid in by_id and cid not in used and len(used) < MAX_PER_MESSAGE:
            used.append(cid)
            return by_id[cid]["sentence"]
        return ""

    text = PLACEHOLDER.sub(fill, body)
    missing = [c["cid"] for c in claims if c["cid"] not in used][: MAX_PER_MESSAGE - len(used)]
    extra = [by_id[cid]["sentence"] for cid in missing]
    used += missing
    if extra:
        text = _insert_before_closing(text, " ".join(extra))
    notes = " ".join(n.strip() for n in notes if n and n.strip() and n.strip() not in text)
    if notes:
        text = _insert_before_closing(text, notes)
    if sheets and "{{SHEETS}}" not in body and sheet_lines(case, sheets) not in text:
        text = _insert_before_closing(text, sheet_lines(case, sheets))
    if questions and "{{QUESTIONS}}" not in body:
        q = " ".join(x.strip() for x in questions if x.strip() and x.strip() not in text)
        if q:
            text = _insert_before_closing(text, q)
    text = _dedupe_questions(text)
    refs = []
    for cid in used:
        refs += [i for i in by_id[cid]["ids"] if i not in refs]
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:BODY_LIMIT], refs[:20], used


def _insert_before_closing(text, block):
    lines = text.rstrip().splitlines()
    for i in range(len(lines) - 1, max(-1, len(lines) - 4), -1):
        if re.match(r"\s*(best regards|kind regards|regards|sincerely|thank you|many thanks)", lines[i], re.I):
            return "\n".join(lines[:i] + ["", block, ""] + lines[i:])
    return (text.rstrip() + "\n\n" + block).strip()


SIGNATURE = "Best regards\nHollerau Pumpenwerke GmbH, Purchasing"
SIGNATURE_FALLBACK = (
    "Dear colleagues,\nthank you for your message. Before we send a new proposal, could you tell us where you "
    "see flexibility - on price, payment terms or contract length?\n" + SIGNATURE
)


def template(case, tone, tactic, has_sheets, ask_breakdown, first, name=None):
    """A deterministic body with placeholders, used without a model or when its text is unusable."""
    greet = f"Dear {name}," if name else "Dear colleagues,"
    if first:
        opener = {
            "warm": "thank you for your quote and for the good cooperation over the years.",
            "firm": "thank you for your quote. We have reviewed it carefully and cannot accept it as it stands.",
        }.get(tone, "thank you for your quote, which we have reviewed carefully.")
    else:
        opener = {
            "warm": "thank you for your reply - we appreciate the step you made.",
            "firm": "thank you for your reply. We are not there yet.",
        }.get(tone, "thank you for your reply.")
    middle = {
        "trade": "We have put together packages that give you more of what matters to you, in exchange for a better "
        "price.",
        "hold": "We have looked again at our position and would ask you to reconsider yours.",
        "close_push": "We are close. One more step from your side and we can sign.",
        # the model's favourite tactic: no cost question here - after a refusal it reads as not listening
        "probe": "We have moved towards you again. Which of the packages below works best for you?",
        "concede": "We have moved towards you again and would ask you to take a step as well.",
        "review": "We are reviewing your offer carefully and will come back to you shortly.",
        "conclude": "We would like to conclude on the alternative package you offered, exactly as set out below. "
        "Please confirm it so that we can place the agreement.",
    }.get(tactic, "We would like to reach an agreement on the following basis.")
    if tactic in ("probe", "concede") and (first or not has_sheets):
        middle = (
            "We have put together the packages below. Which of them works best for you?"
            if has_sheets
            else "We would like to reach an agreement and look forward to your view."
        )
    parts = [greet, opener, middle, "{{CLAIM_BLOCK}}"]
    if has_sheets:
        parts.append("{{SHEETS}}")
    if ask_breakdown:
        parts.append("Could you send us a cost breakdown for this part?")
    parts.append("{{QUESTIONS}}")
    parts.append(SIGNATURE)
    return "\n".join(parts)


def finish_template(body, claims):
    claim_marks = " ".join(f"{{{{CLAIM:{c['cid']}}}}}" for c in claims[:MAX_PER_MESSAGE])
    return body.replace("{{CLAIM_BLOCK}}", claim_marks)


SIGNOFF = re.compile(
    r"^(?:mit freundlichen gr|freundliche gr|viele gr|beste gr|gru|best regards|kind regards|regards)", re.I
)
NAME = re.compile(r"^[A-ZÄÖÜ][\w\-'’]+(?: [A-ZÄÖÜ][\w\-'’]+){1,2}$")


def contact_name(events):
    """The person who signed the supplier's latest e-mail (the line after the sign-off), else None."""
    for msg in reversed(events or []):
        if msg.get("from") == "buyer":
            continue
        lines = [x.strip() for x in (msg.get("body") or "").splitlines() if x.strip()]
        for i, line in enumerate(lines[:-1]):
            if SIGNOFF.match(line):
                cand = lines[i + 1]
                return cand if NAME.match(cand) and not LEAKS.search(cand) else None
        return None
    return None


VOLUME_ASK = re.compile(
    r"which volume|what volume|volume (?:figures|numbers)|exact (?:figures|numbers)|how many (?:pieces|units|parts)|"
    r"volume can you commit|commit to\b[^.?!]*\bvolume",
    re.I,
)
UNIT = re.compile(r"Need: [\d,.]+ ([A-Za-z]+) a year")


def volume_answer(case, sheets, supplier_text):
    """The binding volume, in figures, when the supplier asks for it. Suppliers asked "Which volume can you
    commit to?" in five practice cases and got prose without a number; one walked away. The volume is a term of
    our sheets (or fixed by the enquiry), so stating it leaks nothing."""
    if not VOLUME_ASK.search(supplier_text or ""):
        return None
    fixed = case.get("fixed_terms") or {}

    def term(field):
        values = {(t or {}).get(field, fixed.get(field)) for t in sheets or [{}]}
        return values.pop() if len(values) == 1 else None

    volume = term("volume_per_year")
    if volume is None and "volume_per_year" not in case.get("negotiable", []):
        volume = (case.get("buyer") or {}).get("need_per_year")
    if not volume:
        return None
    unit = (UNIT.search(case.get("briefing") or "") or [None, "units"])[1]
    months = term("contract_months")
    text = f"Our binding volume is {int(volume):,} {unit} a year"
    if months:
        text += f" over {int(months)} months, {round(volume * months / 12):,} {unit} in total"
    elif months == 0:
        text += ", as a single order"
    return text + "."


def subject(case, threads_subject=None):
    base = threads_subject or case.get("title") or "Your quote"
    base = re.sub(r"^(re:\s*)+", "", base, flags=re.I)
    return ("Re: " + base)[:SUBJECT_LIMIT]
