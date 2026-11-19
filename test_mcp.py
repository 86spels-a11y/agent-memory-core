"""Smoke tests: MCP tool functions are plain callables, so we test them directly."""
import os

os.environ["AGENT_MEMORY_DB"] = ":memory:"

import memory_mcp as m


def test_add_and_get_fact_tool():
    m.add_fact("bot1", "user", "name", "Alice")
    fact = m.get_fact("bot1", "user", "name")
    assert fact["value"] == "Alice"


def test_event_tools():
    r = m.log_event("bot1", "ping", "hello", {"n": 1})
    events = m.get_recent_events(limit=5, agent_id="bot1")
    assert events[0]["id"] == r["event_id"]
    assert events[0]["context"] == {"n": 1}


def test_trace_cause_tool():
    a = m.log_event("bot1", "decision", "A")["event_id"]
    b = m.log_event("bot1", "outcome", "B", caused_by=a)["event_id"]
    chain = m.trace_cause(b)
    assert [e["id"] for e in chain] == [b, a]


def test_as_of_tool():
    m.add_fact("bot1", "user", "age", 41)
    t = m.get_fact("bot1", "user", "age")["updated_at"]
    m.add_fact("bot1", "user", "age", 42)
    old = m.get_fact_as_of("bot1", "user", "age", t)
    assert old["value"] == 41
