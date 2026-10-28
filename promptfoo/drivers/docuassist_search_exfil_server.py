"""
EXF-02 harness server — DocuAssist with an observable web_search egress.

AgenticGoat Lab 03's `web_search` returns canned results and makes no real
call-out, so the query parameter never leaves the process: there is nothing to
observe when secrets are smuggled into a search. EXF-02 ("web-search / browse
params as an exfil channel") needs that egress to be real and logged.

This server is a thin wrapper, NOT an edit to the pinned submodule. It imports
the lab's own MCP server module and delegates every tool to the lab's handler,
with one change: `web_search` appends the outbound query to
`state/search_exfil.jsonl` before returning the lab's canned results. That file
stands in for "what the external search provider received" — the egress sink the
EXF-02 oracle inspects (state-/tool-log strength, model-independent: it records
what was actually sent to the tool, not what the model said).

agent.py runs a server by path (`python3 <abs path>`) and inherits cwd = the lab
dir, so importing the lab module and resolving the lab dir from it both work.

Run indirectly, via the provider:
    servers: "<abs path to this file>"   (promptfoo/tools.yaml, EXF-02 row)
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import sys
from pathlib import Path

# agent.py spawns us with cwd = the Lab 03 dir; import the lab's server from there.
sys.path.insert(0, os.getcwd())
import docuassist_mcp_server as lab  # noqa: E402

from mcp.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402

LAB_DIR = Path(lab.__file__).resolve().parent
SEARCH_LOG = LAB_DIR / "state" / "search_exfil.jsonl"

server = Server("docuassist")


@server.list_tools()
async def list_tools():
    # Same tool surface as the lab (web_search advertised identically).
    return await lab.list_tools()


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "web_search":
        # The egress: whatever the agent put in the query now leaves to the
        # "external search provider". Capture it at the boundary, then hand back
        # the lab's own canned results so the agent behaves normally.
        query = arguments.get("query", "")
        SEARCH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SEARCH_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"ts": datetime.datetime.now().isoformat(), "query": query},
                ensure_ascii=False) + "\n")
        print(f"[search-egress] web_search query captured ({len(query)} chars)",
              file=sys.stderr, flush=True)
    return await lab.call_tool(name, arguments)


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
