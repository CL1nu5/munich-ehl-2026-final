"""Diagnostician: deterministic lessons from one finished episode.

Each lesson has a scope (global, case_type, goal, archetype, supplier), a polarity (do / avoid), a
lever and a code; codes also drive policy knobs (see `consolidate.knobs`).
"""

WEAK = 0.3  # supplier price move (%) below which we call it "did not move"


def lesson(level, value, polarity, lever, code, text, case_id, weight=1.0):
    return {
        "scope": {"level": level, "value": value or ""},
        "polarity": polarity,
        "lever": lever,
        "code": code,
        "text": text,
        "case": case_id,
        "weight": weight,
    }


def _scopes(ep, sid):
    """Where a supplier-specific observation applies: its archetypes and the supplier itself."""
    tags = (ep["suppliers"].get(sid) or {}).get("tags") or []
    return [("archetype", t) for t in tags] + [("supplier", sid)]


def _kinds_for_results(log, sid, refs, n):
    """The claim kind behind each of `n` claim results, or None where it cannot be told for sure.

    The arena reports one result per argument (our claims in body order); the simulator one per ID
    (refs first, at most 3). Anything that matches neither count is not attributed."""
    entry = next(
        (e for e in log if e.get("sup") == sid and set(e.get("refs") or []) == set(refs) and e.get("claims")),
        None,
    )
    if entry is None:
        return [None] * n
    claims = entry["claims"]
    if n == len(claims):
        return [c["kind"] for c in claims]
    owner = {i: c["kind"] for c in claims for i in c["ids"]}
    order = list(dict.fromkeys(refs))[:3]
    if n == len(order):
        return [owner.get(i) for i in order]
    return [None] * n


def diagnose(ep):
    out = []
    cid = ep["case_id"]
    for sid, replies in (ep.get("replies") or {}).items():
        first_lesson = len(out)
        statuses = [r.get("status") for r in replies]
        tags = ", ".join((ep["suppliers"].get(sid) or {}).get("tags") or []) or "untagged"
        # Pushed after a final offer and the supplier walked.
        for i, status in enumerate(statuses):
            if status == "walked_away" and i > 0 and statuses[i - 1] == "final_offer":
                for level, value in _scopes(ep, sid) + [("case_type", ep.get("type"))]:
                    out.append(
                        lesson(
                            level,
                            value,
                            "avoid",
                            "closing",
                            "WALKED_AFTER_PUSH",
                            f"Pushing after a final offer made a supplier ({tags}) walk away; accept a "
                            "final offer that beats our alternative.",
                            cid,
                        )
                    )
                break
        # Walked or declined before any final offer: our asks were too aggressive.
        if statuses and statuses[-1] in ("walked_away", "no_bid") and "final_offer" not in statuses:
            for level, value in _scopes(ep, sid):
                out.append(
                    lesson(
                        level,
                        value,
                        "avoid",
                        "price",
                        "WALKED_EARLY",
                        f"A supplier ({tags}) walked away without a final offer; open closer to its "
                        "offer is a hypothesis; departure may have other causes.",
                        cid,
                    )
                )
        # Agreed to our very first ask: we anchored too softly.
        if statuses[:1] == ["agreed"]:
            for level, value in _scopes(ep, sid):
                out.append(
                    lesson(
                        level,
                        value,
                        "do",
                        "price",
                        "TOO_SOFT",
                        f"A supplier ({tags}) agreed to our first ask; a lower anchor is an untested hypothesis.",
                        cid,
                    )
                )
        # How claims landed.
        for i, r in enumerate(replies):
            results = r.get("claims") or []
            kinds = _kinds_for_results(ep.get("log") or [], sid, r.get("refs") or [], len(results))
            for kind, result in zip(kinds, results, strict=True):
                if result == "contradicted" and kind:
                    out.append(
                        lesson(
                            "global",
                            "",
                            "avoid",
                            "claims",
                            f"CONTRADICTED:{kind}",
                            f"A '{kind}' data claim came back contradicted; do not cite that kind "
                            "without an exact match.",
                            cid,
                        )
                    )
            if results and "verified" in results:
                before = replies[i - 1].get("price") if i > 0 else (ep.get("opening") or {}).get(sid)
                after = r.get("price")
                kind = "/".join(sorted({k for k in kinds if k}))
                if before and after and kind:
                    move = (before - after) / before * 100
                    for level, value in _scopes(ep, sid):
                        if move >= 1.0:
                            out.append(
                                lesson(
                                    level,
                                    value,
                                    "do",
                                    "claims",
                                    f"CLAIM_MOVED:{kind}",
                                    f"Price fell after verified {kind} arguments ({tags}); "
                                    "changed terms and other causes remain confounded.",
                                    cid,
                                    0.7,
                                )
                            )
                        elif move < WEAK:
                            out.append(
                                lesson(
                                    level,
                                    value,
                                    "avoid",
                                    "claims",
                                    f"CLAIM_IGNORED:{kind}",
                                    f"Price did not fall after verified {kind} arguments ({tags}); "
                                    "this does not establish that they were ineffective.",
                                    cid,
                                    0.7,
                                )
                            )
        # When it reached its final offer: our concession horizon.
        if "final_offer" in statuses:
            r = statuses.index("final_offer") + 1
            for level, value in _scopes(ep, sid):
                out.append(
                    lesson(
                        level,
                        value,
                        "do",
                        "timing",
                        f"FINAL_AT:{r}",
                        f"A supplier ({tags}) gave its final offer after {r} of our messages; plan "
                        "concessions to land by then.",
                        cid,
                        0.5,
                    )
                )
        for item in out[first_lesson:]:
            item["supplier"] = sid
    return out
