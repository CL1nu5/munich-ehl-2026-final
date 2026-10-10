"""A local, read-only MCP server over the data package (stdio).

Plug it into any MCP-capable agent framework or chat client so the model can explore the
company's data itself. Six tools, all read-only.
"""

import json
import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .data import DataPackage


def build(root):
    pkg = DataPackage(root)
    mcp = MCPServer("arena-data", instructions="Read-only data of a fictional pump maker's procurement.")
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    @mcp.tool(annotations=read_only)
    def list_tables() -> str:
        """All tables of the data package with their columns and row counts."""
        return json.dumps(pkg.tables())

    @mcp.tool(annotations=read_only)
    def query(sql: str) -> str:
        """Run one read-only SQL query (SELECT or WITH) on the data package. Max 200 rows."""
        return json.dumps(pkg.query(sql), default=str)

    @mcp.tool(annotations=read_only)
    def lookup(id: str) -> str:
        """The row behind a citable ID such as po:4500012345 or idx:GP19-245113@2026-08."""
        return json.dumps(pkg.lookup(id), default=str)

    @mcp.tool(annotations=read_only)
    def list_ids(prefix: str) -> str:
        """All citable IDs starting with a prefix, e.g. 'idx:GP19-245113@' for a whole series."""
        return json.dumps(pkg.ids(prefix)[:500])

    @mcp.tool(annotations=read_only)
    def read_document(ref: str) -> str:
        """A document (letter, e-mail, meeting or call note, policy) by ID or by path."""
        return pkg.document(ref)

    @mcp.tool(annotations=read_only)
    def get_case(case_id: str = "") -> str:
        """One practice case file by ID, or the list of practice cases if no ID is given."""
        if case_id:
            return json.dumps(pkg.cases[case_id])
        return json.dumps([{k: c.get(k) for k in ("case_id", "type", "title")} for c in pkg.cases.values()])

    return mcp


def main(root=None):
    build(root or os.environ["ARENA_DATA"]).run()  # stdio transport


if __name__ == "__main__":
    main()
