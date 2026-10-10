"""Conservative run-local hypotheses: independent cases, no score-based causal credit."""

import hashlib
import math

from .diagnose import diagnose

SPECIFICITY = {"supplier": 4.0, "archetype": 3.0, "case_type": 2.0, "goal": 2.0, "global": 1.0}
CAP_PER_SCOPE = 6
MIN_CASES = 3


def key_of(lesson):
    scope = lesson["scope"]
    tail = lesson.get("code") or hashlib.sha1(lesson["text"].lower().encode()).hexdigest()[:8]
    return f"{scope['level']}|{scope['value']}|{lesson['lever']}|{lesson['polarity']}|{tail}"


def subject(item):
    code = item.get("code") or ""
    if code in ("TOO_SOFT", "WALKED_EARLY"):
        return "anchor"
    return None


def merge(episodes):
    merged = {}
    for ep in episodes:
        # A requested close is not an outcome. Support counts distinct confirmed cases only.
        if not ep.get("result"):
            continue
        lessons = diagnose(ep) + (ep.get("reflection") or {}).get("lessons", [])
        for lesson in lessons:
            if not isinstance(lesson, dict) or not lesson.get("text") or not isinstance(lesson.get("scope"), dict):
                continue
            item = {**lesson, "case": ep["case_id"]}
            key = key_of(item)
            cur = merged.setdefault(
                key,
                {
                    **item,
                    "key": key,
                    "cases": [],
                    "suppliers": [],
                    "support": 0,
                    "contradict": 0,
                    "status": "hypothesis",
                },
            )
            if ep["case_id"] not in cur["cases"]:
                cur["cases"].append(ep["case_id"])
            supplier = item.get("supplier")
            if not supplier and item["scope"]["level"] == "supplier":
                supplier = item["scope"]["value"]
            if not supplier and len(ep.get("suppliers", {})) == 1:
                supplier = next(iter(ep["suppliers"]))
            if supplier in ep.get("suppliers", {}) and supplier not in cur["suppliers"]:
                cur["suppliers"].append(supplier)
            cur["support"] = len(cur["cases"])
    for item in merged.values():
        opposite_cases = set()
        for other in merged.values():
            if subject(item) and subject(item) == subject(other) and item["scope"] == other["scope"]:
                if item["polarity"] != other["polarity"]:
                    opposite_cases.update(other["cases"])
        item["contradict"] = len(opposite_cases)
        # Beta(1,1) shrinkage: one observation never has confidence 1.
        item["confidence"] = round((1 + item["support"]) / (2 + item["support"] + item["contradict"]), 3)
        enough = item["support"] >= MIN_CASES and (item["scope"]["level"] == "supplier" or len(item["suppliers"]) >= 2)
        item["status"] = "retired" if item["contradict"] >= item["support"] else "active" if enough else "hypothesis"
    return merged


def retrieve(merged, case, tags_by_supplier, k=10, cap=CAP_PER_SCOPE):
    tags = {t for values in tags_by_supplier.values() for t in values}
    goal = case.get("buyer", {}).get("category_goal")
    ranked = []
    for item in merged.values():
        if item["status"] != "active":
            continue
        level, value = item["scope"]["level"], item["scope"]["value"]
        match = (
            level == "global"
            or (level == "supplier" and value in tags_by_supplier)
            or (level == "archetype" and value in tags)
            or (level == "case_type" and value == case.get("type"))
            or (level == "goal" and value == goal)
        )
        if match:
            ranked.append((SPECIFICITY.get(level, 1) * item["confidence"] * math.log1p(item["support"]), item))
    ranked.sort(key=lambda x: (-x[0], x[1]["key"]))
    out, counts = [], {}
    for _, item in ranked:
        slot = (item["scope"]["level"], item["scope"]["value"])
        if counts.get(slot, 0) >= cap:
            continue
        counts[slot] = counts.get(slot, 0) + 1
        out.append(item)
        if len(out) >= k:
            break
    return out


def knobs(lessons, tags_by_supplier, case=None):
    out = {
        "allow_final_push": True,
        "drop_mult": {},
        "blocked_claims": [],
        "blocked_refs": [],
        "horizon": {},
        "tone": {},
    }
    case = case or {}
    for sid, tags in tags_by_supplier.items():
        scopes = {
            ("supplier", sid),
            ("case_type", case.get("type")),
            ("goal", case.get("buyer", {}).get("category_goal")),
            ("global", ""),
        } | {("archetype", t) for t in tags}
        soft, walk, finals = set(), set(), {}
        for item in lessons:
            if item.get("status") != "active" or (item["scope"]["level"], item["scope"]["value"]) not in scopes:
                continue
            code = item.get("code") or ""
            if code == "WALKED_AFTER_PUSH":
                out["allow_final_push"] = False
            elif code == "TOO_SOFT":
                soft.update(item["cases"])
            elif code == "WALKED_EARLY":
                walk.update(item["cases"])
            elif code.startswith("FINAL_AT:"):
                for cid in item["cases"]:
                    finals[cid] = int(code.split(":")[1])
        # Bounded by 10% regardless of how many correlated tags/rounds a case produced.
        out["drop_mult"][sid] = 1.1 if len(soft) > len(walk) else 0.9 if len(walk) > len(soft) else 1.0
        if len(finals) >= MIN_CASES:
            rounds = sorted(finals.values())
            out["horizon"][sid] = max(3, rounds[len(rounds) // 2] + 1)
    return out


def view(episodes, case, tags_by_supplier, k=10):
    merged = merge(episodes)
    selected = retrieve(merged, case, tags_by_supplier, k)
    applicable = retrieve(merged, case, tags_by_supplier, k=10_000, cap=10_000)
    return {
        "episodes": len(episodes),
        "hypotheses": sum(x["status"] == "hypothesis" for x in merged.values()),
        "lessons": [
            {key: item[key] for key in ("key", "scope", "polarity", "lever", "text", "confidence", "support", "cases")}
            for item in selected
        ],
        "knobs": knobs(applicable, tags_by_supplier, case),
    }
