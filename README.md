# agent-memory-core

Sovereign memory core for AI agents. SQLite only, no ORM, no LLM dependency.

## Why

Most agent-memory libraries require an LLM to extract facts on every write
(Mem0, Letta) or run a vector DB. This core gives the agent **deterministic
primitives** it can call directly:

- zero token cost per write
- auditable, reproducible state
- no background daemon

## Install

Requires Python 3.12+.

\`\`\`bash
python3 -m venv .venv
.venv/bin/pip install pytest   # dev only
\`\`\`

## Usage

\`\`\`python
from memory_core import MemoryCore

m = MemoryCore("agent.db")

# structured facts (UPSERT by agent_id + category + key)
m.add_fact("bot1", "user", "name", "Alice")
m.get_fact("bot1", "user", "name")   # -> dict with value, confidence, timestamps

# events with JSON context
m.log_event("bot1", "message", "hello", context={"tokens": 42})
m.get_recent_events(limit=5, agent_id="bot1")
\`\`\`

## Design decisions

### WAL is enabled once per DB file, not per connection

`PRAGMA journal_mode=WAL` is a **file property**, not a connection property.
Re-running it on every new connection causes unnecessary contention for an
exclusive lock — and that lock **ignores `busy_timeout`**. See
[SQLite docs](https://www.sqlite.org/wal.html) and community reports:
prismal-ai/prismal, trevorotts1/openclaw-onboarding, msitarzewski/fintick
all hit this.

Our `_ensure_wal()` checks the current mode first, switches only if needed,
and retries with backoff. `_get_connection()` sets `busy_timeout` **before**
any other PRAGMA.

### `BEGIN IMMEDIATE` is avoided

In WAL mode it can return `database is locked` immediately, bypassing
`busy_timeout`. We use `with conn:` (implicit deferred transactions) instead.

## Tests

\`\`\`bash
.venv/bin/python -m pytest test_memory.py -v
\`\`\`

Covers:
- fact UPSERT (created_at preserved, updated_at bumped)
- cross-agent isolation of facts
- JSON context round-trip for events
- per-agent event filtering
- concurrent writers within a process
- concurrent writers across processes (real WAL contention)
- WAL enabled on new DB
- re-init on existing WAL DB

## Status

Pre-1.0. API may change. See roadmap below.

## Roadmap

- [ ] `get_fact_as_of()` — historical queries ("what did we know at T?")
- [ ] causal edges (`caused_by`, `trace_cause()`) — decision → outcome links
- [ ] TTL / `prune()` — controlled forgetting via `confidence`
