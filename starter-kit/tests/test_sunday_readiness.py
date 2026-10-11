import csv
import json
from pathlib import Path

from agents._learner.dossier import digest, supplier_dossier
from arena_kit.data import DataPackage

DATA = Path(__file__).resolve().parents[2] / "data"


def test_sunday_facts_are_loaded_without_overwriting_base():
    base = json.loads((DATA / "facts.json").read_text())
    new = json.loads((DATA / "release-sunday/facts.json").read_text())
    package = DataPackage(DATA)
    assert new and not base.keys() & new.keys()
    assert package.facts == base | new
    for ref, fact in new.items():
        assert package.lookup(ref) == fact


def test_latest_supplier_news_reaches_model_digest():
    package = DataPackage(DATA)
    with (package.release / "data/news.csv").open() as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        sid = row.get("supplier_id")
        if not sid or row["topic"] in ("noise", "market"):
            continue
        dossier = supplier_dossier(package, sid, [])
        compact = digest({"suppliers": {sid: dossier}}, sid)
        assert row["headline"] in [n["headline"] for n in compact["news"]]
        assert dossier["news"] == sorted(dossier["news"], key=lambda n: (n["date"], n["id"]), reverse=True)


def test_sunday_letters_and_market_memo_readable():
    package = DataPackage(DATA)
    with (package.release / "data/documents.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert rows
    for row in rows:
        assert package.document(row["id"]).strip()
        if row.get("supplier_id"):
            d = supplier_dossier(package, row["supplier_id"], [])
            assert row["id"] in {x["id"] for x in d["documents"]}


def test_existing_key_is_mapped_in_memory_only(tmp_path, monkeypatch):
    import run_local

    secret_file = tmp_path / ".env.local"
    original = "OPENAI_API_KEY='synthetic-test-value'\n"
    secret_file.write_text(original)
    monkeypatch.setattr(run_local, "ROOT", tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    run_local.load_credentials()
    assert run_local.os.environ["LLM_API_KEY"] == "synthetic-test-value"
    assert secret_file.read_text() == original


def test_explicit_model_key_is_not_overwritten(tmp_path, monkeypatch):
    import run_local

    (tmp_path / ".env.local").write_text("OPENAI_API_KEY='synthetic-file-value'\n")
    monkeypatch.setattr(run_local, "ROOT", tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "synthetic-explicit-value")
    run_local.load_credentials()
    assert run_local.os.environ["LLM_API_KEY"] == "synthetic-explicit-value"
