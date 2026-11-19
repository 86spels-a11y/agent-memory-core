"""MCP server exposing MemoryCore to Claude Desktop / any MCP client.

Run:  .venv/bin/python memory_mcp.py
Config (claude_desktop_config.json):
  {
    "mcpServers": {
      "agent-memory": {
        "command": "/path/to/.venv/bin/python",
        "args": ["/path/to/memory_mcp.py"],
        "env": {"AGENT_MEMORY_DB": "/path/to/agent.db"}
      }
    }
  }
"""
from __future__ import annotations

import os
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from memory_core import MemoryCore

DB_PATH = os.environ.get("AGENT_MEMORY_DB", "agent.db")
core = MemoryCore(DB_PATH)
mcp = FastMCP("agent-memory")


@mcp.tool()
def add_fact(
    agent_id: str, category: str, key: str, value: Any, confidence: float = 1.0
) -> dict:
    """Store or update a fact for an agent. UPSERT by (agent_id, category, key)."""
    core.add_fact(agent_id, category, key, value, confidence)
    return core.get_fact(agent_id, category, key)


@mcp.tool()
def get_fact(agent_id: str, category: str, key: str) -> Optional[dict]:
    """Read the current value of a fact, or null if absent."""
    return core.get_fact(agent_id, category, key)


@mcp.tool()
def get_fact_as_of(
    agent_id: str, category: str, key: str, timestamp: str
) -> Optional[dict]:
    """Read what the fact was at timestamp (ISO-8601, e.g. '2026-01-01T00:00:00+00:00')."""
    return core.get_fact_as_of(agent_id, category, key, timestamp)


@mcp.tool()
def get_fact_history(agent_id: str, category: str, key: str) -> list[dict]:
    """List all archived versions of a fact, oldest first."""
    return core.get_fact_history(agent_id, category, key)


@mcp.tool()
def log_event(
    agent_id: str,
    event_type: str,
    summary: str,
    context: Optional[dict] = None,
    caused_by: Optional[int] = None,
    relation: str = "caused",
) -> dict:
    """Append an event. Optionally link to a parent event via caused_by.

    relation: 'caused' | 'enabled' | 'prevented'
    """
    event_id = core.log_event(
        agent_id, event_type, summary, context, caused_by, relation
    )
    return {"event_id": event_id}


@mcp.tool()
def get_recent_events(
    limit: int = 10, agent_id: Optional[str] = None
) -> list[dict]:
    """Recent events, newest first. Filter by agent_id to avoid cross-agent leaks."""
    return core.get_recent_events(limit, agent_id)


@mcp.tool()
def trace_cause(event_id: int) -> list[dict]:
    """Walk the causal chain upward from event_id (cycle-safe)."""
    return core.trace_cause(event_id)


if __name__ == "__main__":
    mcp.run()
