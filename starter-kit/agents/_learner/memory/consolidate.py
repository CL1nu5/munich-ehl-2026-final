"""Consolidator + Retriever: merge the run's lessons, score them, and pick what applies to a case.

Recursive within a run: a closing reflection reports whether the lessons it used held, and the
case's score confirms or contradicts them, so later cases see lessons weighted by outcomes.
"""

import hashlib
import math

from .diagnose import diagnose

SPECIFICITY = {"supplier": 4.0, "archetype": 3.0, "case_type": 2.0, "goal": 2.0, "global": 1.0}
CAP_PER_SCOPE = 6


def key_of(lesson):
    s = lesson["scope"]
    tail = lesson.get("code") or hashlib.sha1(lesson["text"].lower().encode()).hexdigest()[:8]
    return f"{s['level']}|{s['value']}|{lesson['lever']}|{lesson['polarity']}|{tail}"


def subject(item):
    """What a lesson is about, for polarity contradictions; None = never contradicted by polarity."""
    code = item.get("code")
    if not code:
        return None  # free-text model lessons: only outcomes (held/failed, scores) retire them
    head, _, tail = code.partition(":")
    if head in ("CLAIM_MOVED", "CLAIM_IGNORED"):
        return f"claim:{tail}"
    if head in ("TOO_SOFT", "WALKED_EARLY"):
        return "anchor"
    return None


def merge(episodes):
    """{key: lesson with support/contradict/cases} from every episode of the run."""
    merged = {}

    def add(item, weight=1.0):
        k = key_of(item)
        cur = merged.get(k)
        if cur is None:
            cur = merged[k] = {**item, "key": k, "support": 0.0, "contradict": 0.0, "cases": []}
        cur["support"] += weight
        cur["text"] = item["text"]  # latest wording wins
        if item.get("case") and item["case"] not in cur["cases"]:
            cur["cases"].append(item["case"])

    feedback = []
    for ep in episodes:
        for item in diagnose(ep):
            add(item, item.get("weight", 1.0))
        refl = ep.get("reflection") or {}
        for item in refl.get("lessons") or []:
            if isinstance(item, dict) and item.get("text") and isinstance(item.get("scope"), dict):
                add({**item, "case": ep["case_id"]}, 1.0)
        # lessons the case used: held / failed by its reflection, else by its score
        used = {k for entry in ep.get("log") or [] for k in entry.get("lessons") or []}
        verdicts = refl.get("feedback") or {}
        score = (ep.get("result") or {}).get("score")
        for k in used:
            v = verdicts.get(k)
            if v == "held" or (v is None and score is not None and score >= 0.7):
                feedback.append((k, "support", 1.0 if v else 0.5))
            elif v == "failed" or (v is None and score is not None and score < 0.3):
                feedback.append((k, "contradict", 1.0 if v else 0.5))
    for k, field, weight in feedback:
        if k in merged:
            merged[k][field] += weight
    # opposite advice about the same thing (same scope, lever and subject) contradicts each other
    by_slot = {}
    for item in merged.values():
        subj = subject(item)
        if subj is None:
            continue  # guards (contradicted data, walked after a push, ...) only yield to outcomes
        slot = (item["scope"]["level"], item["scope"]["value"], item["lever"], subj)
        by_slot.setdefault(slot, []).append(item)
    for items in by_slot.values():
        for a in items:
            a["contradict"] += sum(b["support"] for b in items if b["polarity"] != a["polarity"]) * 0.5
    for item in merged.values():
        item["confidence"] = round(item["support"] / (item["support"] + item["contradict"] + 1e-9), 2)
        item["status"] = "retired" if item["contradict"] > item["support"] else "active"
    return merged


def retrieve(merged, case, tags_by_supplier, k=10, cap=CAP_PER_SCOPE):
    """Active lessons that apply to this case, most specific and best supported first."""
    tags = {t for ts in tags_by_supplier.values() for t in ts}
    sups = set(tags_by_supplier)
    goal = (case.get("buyer") or {}).get("category_goal")
    scored = []
    for item in merged.values():
        if item["status"] != "active":
            continue
        level, value = item["scope"]["level"], item["scope"]["value"]
        match = (
            level == "global"
            or (level == "archetype" and value in tags)
            or (level == "supplier" and value in sups)
            or (level == "case_type" and value == case.get("type"))
            or (level == "goal" and value == goal)
        )
        if match:
            score = SPECIFICITY.get(level, 1.0) * item["confidence"] * math.log1p(item["support"])
            scored.append((score, item))
    scored.sort(key=lambda x: -x[0])
    out, per_scope = [], {}
    for _, item in scored:
        slot = (item["scope"]["level"], item["scope"]["value"])
        if per_scope.get(slot, 0) >= cap:
            continue
        per_scope[slot] = per_scope.get(slot, 0) + 1
        out.append(item)
        if len(out) >= k:
            break
    return out


def knobs(lessons, tags_by_supplier, case=None):
    """Policy parameters the deterministic code applies (so learning works without the model too).
    Each observed case counts once per code, however many scopes (tags) it was recorded under."""
    out = {"allow_final_push": True, "drop_mult": {}, "blocked_claims": [], "horizon": {}, "tone": {}}
    case = case or {}
    for sid, tags in tags_by_supplier.items():
        scopes = {
            ("supplier", sid),
            ("case_type", case.get("type")),
            ("goal", (case.get("buyer") or {}).get("category_goal")),
        } | {("archetype", t) for t in tags}
        soft, walk, finals, own_finals = set(), set(), {}, {}
        for item in lessons:
            slot = (item["scope"]["level"], item["scope"]["value"])
            code = item.get("code") or ""
            if slot not in scopes and item["scope"]["level"] != "global":
                continue
            cases = set(item.get("cases") or [])
            if code == "WALKED_AFTER_PUSH":
                out["allow_final_push"] = False
            elif code == "WALKED_EARLY":
                walk |= cases
            elif code == "TOO_SOFT":
                soft |= cases
            elif code.startswith("FINAL_AT:"):
                target = own_finals if slot == ("supplier", sid) else finals
                for c in cases:
                    target[c] = min(target.get(c, 99), int(code.split(":")[1]))
        mult = 0.75 ** min(len(walk), 2) * 1.25 ** min(len(soft), 2)
        out["drop_mult"][sid] = round(max(0.5, min(2.0, mult)), 3)
        rounds = sorted(own_finals.values()) or (sorted(finals.values()) if len(finals) >= 2 else [])
        if rounds:
            median = rounds[len(rounds) // 2]
            out["horizon"][sid] = max(3, median + 1)  # land concessions around when it usually stops
    for item in lessons:
        code = item.get("code") or ""
        if code.startswith("CONTRADICTED:") and code.split(":", 1)[1] not in ("data", ""):
            out["blocked_claims"].append(code.split(":", 1)[1])
    out["blocked_claims"] = sorted(set(out["blocked_claims"]))
    return out


def view(episodes, case, tags_by_supplier, k=10):
    merged = merge(episodes)
    lessons = retrieve(merged, case, tags_by_supplier, k)
    applicable = retrieve(merged, case, tags_by_supplier, k=10_000, cap=10_000)
    return {
        "episodes": len(episodes),
        "lessons": [
            {
                "key": x["key"],
                "scope": x["scope"],
                "polarity": x["polarity"],
                "lever": x["lever"],
                "text": x["text"],
                "confidence": x["confidence"],
                "support": round(x["support"], 2),
                "cases": x["cases"][:5],
            }
            for x in lessons
        ],
        "knobs": knobs(applicable, tags_by_supplier, case),
    }
