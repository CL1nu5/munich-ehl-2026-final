"""Targeted regression checks for the final edge improvements:
- Anchor drop bounded to safe 24% ceiling (avoiding the 25% insult penalty).
- Immediate recognition of quoted index clauses in round 0 with widest deadband selection.
- Realistic default pacing horizon of 6 rounds.
- inspect.py journal resolution.
"""

from test_negotiation_generalization import scenario

from agents._learner import graph, inspect, offers


def test_anchor_drop_never_exceeds_insult_threshold():
    case = {
        "negotiable": ["unit_price"],
        "buyer": {"current_unit_price": 50.0},
        "type": "price_increase",
    }
    led = {"price": 100.0, "supplier_id": "sup:test"}
    # Even with all positive multipliers that would multiply to > 1.5x:
    tags = ["spare_capacity", "weak_costing", "hedged_forward"]
    dossier = {"parts": {}}
    anchor = offers.anchor_price(case, led, tags, dossier, drop=0.25)
    # Must never drop below 76.0 (24% maximum cut):
    assert anchor >= 76.0
    assert anchor < 100.0


def test_quoted_index_clause_recognized_immediately():
    clause = {"series": "PPI-C2431", "material_share": 0.8, "deadband_pct": 0.0}
    case = {
        "negotiable": ["unit_price", "index_clause"],
        "term_options": {
            "index_clause": [
                None,
                {"series": "PPI-C2431", "material_share": 0.8, "deadband_pct": 0.0},
                {"series": "PPI-C2431", "material_share": 0.8, "deadband_pct": 3.0},
                {"series": "PPI-C2431", "material_share": 0.8, "deadband_pct": 5.0},
            ]
        },
    }
    led = {
        "terms": {"unit_price": 10.0, "index_clause": clause},
        "offer_id": "offer:70740-0",
    }
    pref = graph._with_quoted_index(case, led, {})
    assert "index_clause" in pref["fields"]
    # Widened values must include all non-empty options:
    values = pref["fields"]["index_clause"]["values"]
    assert any(isinstance(v, dict) and v.get("deadband_pct") == 5.0 for v in values)


def test_with_fields_picks_widest_deadband_for_index_clause():
    case, _ = scenario(101)
    opts = [
        {"series": "S1", "material_share": 0.5, "deadband_pct": 0.0},
        {"series": "S1", "material_share": 0.5, "deadband_pct": 5.0},
        {"series": "S1", "material_share": 0.5, "deadband_pct": 2.0},
    ]
    menu = [{"level": "step", "c": 100.0, "terms": {"unit_price": 10.0}}]
    fields = {"index_clause": {"values": opts}}
    result = graph._with_fields(case, menu, "step", fields, {})
    assert result is not None
    assert result["terms"]["index_clause"]["deadband_pct"] == 5.0


def test_inspect_resolve_locates_local_runs(tmp_path):
    run_dir = tmp_path / "test_run_123"
    run_dir.mkdir()
    (run_dir / "run.sqlite").write_text("")
    resolved = inspect.resolve("test_run_123", runs_dir=str(tmp_path))
    assert resolved == run_dir
