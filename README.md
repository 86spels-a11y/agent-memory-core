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

## Features

### Facts
- `add_fact(agent_id, category, key, value)` — UPSERT, archives previous version
- `get_fact(agent_id, category, key)` — current value
- `get_fact_as_of(..., timestamp)` — historical value at time T
- `get_fact_history(...)` — all archived versions

### Events
- `log_event(agent_id, event_type, summary, context=None, caused_by=None, relation="caused")`
- `get_recent_events(limit=10, agent_id=None)` — filtered by agent
- `trace_cause(event_id)` — walks the causal chain upward (cycle-safe)
- Relations: `caused` / `enabled` / `prevented`

## Roadmap

- [ ] Type symmetry: `get_fact` currently returns `value` as str even if
      written as non-str (json-serialized). Either document or `json.loads`.
- [ ] TTL / `prune()` — controlled forgetting via `confidence`
- [ ] `export` / `import` — portable snapshots

## MCP server

`memory_mcp.py` exposes `MemoryCore` as MCP tools for Claude Desktop
(or any MCP client). Configure in `claude_desktop_config.json`:

\`\`\`json
{
  "mcpServers": {
    "agent-memory": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/memory_mcp.py"],
      "env": {"AGENT_MEMORY_DB": "/path/to/agent.db"}
    }
  }
}
\`\`\`

Tools: `add_fact`, `get_fact`, `get_fact_as_of`, `get_fact_history`,
`log_event`, `get_recent_events`, `trace_cause`.

### Termux / Android note

`mcp` depends on `pydantic-core`, which has no PyPI wheel for Android.
Termux's `rust` package is missing `.rlib` files (termux-packages#30426),
so building from source fails with `crate std required to be in rlib format`.

Workaround — install Rust-based deps from the Termux index first:

\`\`\`bash
pip install --index-url https://termux-user-repository.github.io/pypi/ "pydantic-core==2.41.5"
pip install --no-deps "pydantic==2.12.5"
pip install --no-deps "mcp>=1.28,<2"
pip install anyio httpx httpx-sse sse-starlette starlette python-dotenv attrs
pip install --no-deps jsonschema pyjwt python-multipart typing-inspection pydantic-settings uvicorn
pip install referencing jsonschema-specifications
\`\`\`

Linux/macOS users don't need any of this — plain \`pip install mcp\` works.
