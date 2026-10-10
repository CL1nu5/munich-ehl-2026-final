"""Real local MCP subprocess roundtrip and data-boundary regression tests."""

import asyncio
import json
import sqlite3
import sys

import pytest

from arena_kit.data import DataPackage


@pytest.fixture
def data_root(tmp_path):
    root = tmp_path / "package"
    root.mkdir()
    (root / "facts.json").write_text(json.dumps({"cost:1": {"unit_cost": 12}}))
    (root / "note.txt").write_text("Delivery is confirmed.")
    (root / "cases/practice").mkdir(parents=True)
    (root / "cases/practice/case.json").write_text(json.dumps({"case_id": "case:1", "title": "New part"}))
    with sqlite3.connect(root / "world.sqlite") as db:
        db.execute("CREATE TABLE documents (id TEXT, file TEXT)")
        db.execute("INSERT INTO documents VALUES ('doc:1', 'note.txt')")
        db.execute("CREATE TABLE quantities (n INTEGER)")
        db.executemany("INSERT INTO quantities VALUES (?)", [(i,) for i in range(250)])
    return root


def test_document_metadata_cannot_change_trusted_root(data_root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "test-only.txt").write_text("TEST SENTINEL")
    facts = {"doc:escape": {"root": str(outside), "file": "test-only.txt"}}
    (data_root / "facts.json").write_text(json.dumps(facts))
    package = DataPackage(data_root)
    try:
        with pytest.raises(FileNotFoundError):
            package.document("doc:escape")
    finally:
        package.db.close()


@pytest.mark.parametrize("reference", ["../outside.txt", "link.txt"])
def test_document_traversal_and_symlink_are_blocked(data_root, tmp_path, reference):
    outside = tmp_path / "outside.txt"
    outside.write_text("TEST SENTINEL")
    (data_root / "link.txt").symlink_to(outside)
    package = DataPackage(data_root)
    try:
        with pytest.raises(FileNotFoundError):
            package.document(reference)
    finally:
        package.db.close()


@pytest.mark.parametrize("sql", ["", " ", "\n\t"])
def test_blank_query_is_a_clear_validation_error(data_root, sql):
    package = DataPackage(data_root)
    try:
        with pytest.raises(ValueError, match="read-only"):
            package.query(sql)
    finally:
        package.db.close()


@pytest.mark.parametrize("limit", [-1, 0, True, 1.5])
def test_invalid_query_limits_are_rejected(data_root, limit):
    package = DataPackage(data_root)
    try:
        with pytest.raises(ValueError):
            package.query("SELECT * FROM quantities", limit=limit)
    finally:
        package.db.close()


def test_query_cap_cannot_be_bypassed(data_root):
    package = DataPackage(data_root)
    try:
        assert len(package.query("SELECT * FROM quantities", limit=1000)) == 200
        assert len(package.query("SELECT * FROM quantities", limit=5)) == 5
    finally:
        package.db.close()


@pytest.mark.parametrize("with_release", [False, True])
def test_real_mcp_process_roundtrip_errors_concurrency_and_restart(data_root, with_release):
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = {"ARENA_DATA": str(data_root)}
    if with_release:
        release = data_root.parent / "external-release"
        release.mkdir()
        (release / "facts.json").write_text(json.dumps({"doc:sunday": {"file": "news.txt"}}))
        (release / "news.txt").write_text("New Sunday constraints.")
        env["ARENA_RELEASE"] = str(release)

    async def run():
        for _ in range(10):
            params = StdioServerParameters(command=sys.executable, args=["-m", "arena_kit.mcp_server"], env=env)
            async with stdio_client(params) as streams:
                async with ClientSession(*streams, read_timeout_seconds=10) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    assert len(listing.tools) == 6
                    calls = [
                        ("list_tables", {}, "quantities"),
                        ("query", {"sql": "SELECT count(*) AS n FROM quantities"}, "250"),
                        ("lookup", {"id": "cost:1"}, "12"),
                        ("list_ids", {"prefix": "doc:"}, "doc:1"),
                        ("read_document", {"ref": "doc:1"}, "Delivery is confirmed."),
                        ("get_case", {"case_id": "case:1"}, "New part"),
                    ]
                    if with_release:
                        calls.append(("read_document", {"ref": "doc:sunday"}, "New Sunday constraints."))
                    results = await asyncio.gather(*[session.call_tool(n, a) for n, a, _ in calls])
                    for result, (_, _, expected) in zip(results, calls, strict=True):
                        assert not result.is_error
                        assert expected in "".join(getattr(c, "text", "") for c in result.content)
                    for name, args in [
                        ("query", {"sql": "DELETE FROM quantities"}),
                        ("query", {"sql": ""}),
                        ("read_document", {"ref": "../outside.txt"}),
                        ("get_case", {"case_id": "case:missing"}),
                    ]:
                        result = await session.call_tool(name, args)
                        assert result.is_error
                    healthy = await session.call_tool("query", {"sql": "SELECT count(*) AS n FROM quantities"})
                    assert not healthy.is_error
                    assert "250" in "".join(getattr(c, "text", "") for c in healthy.content)

    asyncio.run(asyncio.wait_for(run(), 60))


def test_shared_connection_concurrent_reads_remain_consistent(data_root):
    from concurrent.futures import ThreadPoolExecutor

    package = DataPackage(data_root)

    def read(index):
        if index % 3 == 0:
            return package.ids("doc:") == ["doc:1"]
        if index % 3 == 1:
            return package.tables()["documents"]["rows"] == 1
        return package.query("SELECT count(*) AS n FROM quantities") == [{"n": 250}]

    try:
        with ThreadPoolExecutor(max_workers=16) as executor:
            assert all(executor.map(read, range(300)))
    finally:
        package.db.close()
