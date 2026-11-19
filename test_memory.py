import pytest

from memory_core import MemoryCore


@pytest.fixture
def core():
    return MemoryCore(db_path=":memory:")


def test_add_and_get_fact(core):
    assert core.add_fact("bot1", "user", "name", "Alice") is True
    fact = core.get_fact("bot1", "user", "name")
    assert fact is not None
    assert fact["agent_id"] == "bot1"
    assert fact["category"] == "user"
    assert fact["key"] == "name"
    assert fact["value"] == "Alice"


def test_cross_agent_isolation(core):
    core.add_fact("bot1", "user", "secret", "top-secret")
    assert core.get_fact("bot2", "user", "secret") is None
    fact = core.get_fact("bot1", "user", "secret")
    assert fact is not None
    assert fact["value"] == "top-secret"


def test_event_context(core):
    ctx = {"source": "api", "tokens": 42, "tags": ["a", "b"]}
    event_id = core.log_event("bot1", "message", "hello world", context=ctx)
    assert isinstance(event_id, int)
    events = core.get_recent_events(limit=5)
    assert len(events) == 1
    assert events[0]["context"] == ctx
    assert events[0]["event_type"] == "message"
    assert events[0]["summary"] == "hello world"
    assert isinstance(events[0]["context"], dict)


def test_upsert_updates_timestamp(core):
    core.add_fact("bot1", "user", "name", "Alice")
    first = core.get_fact("bot1", "user", "name")
    core.add_fact("bot1", "user", "name", "Bob")
    second = core.get_fact("bot1", "user", "name")
    assert second["value"] == "Bob"
    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] >= first["updated_at"]


def test_concurrent_writers_wal(tmp_path):
    import threading
    db = str(tmp_path / "conc.db")
    a, b = MemoryCore(db), MemoryCore(db)
    errs = []

    def writer(core, prefix):
        try:
            for i in range(50):
                core.add_fact("bot", "k", f"{prefix}{i}", i)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    t1 = threading.Thread(target=writer, args=(a, "a"))
    t2 = threading.Thread(target=writer, args=(b, "b"))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert not errs, errs
    assert a.get_fact("bot", "k", "a49")["value"] == 49
    assert a.get_fact("bot", "k", "b49")["value"] == 49


def test_concurrent_processes_wal(tmp_path):
    import subprocess
    import sys
    db = str(tmp_path / "proc.db")
    script = (
        "import sys; from memory_core import MemoryCore; "
        "m = MemoryCore(sys.argv[1]); "
        "[m.add_fact('bot','k',f'{sys.argv[2]}{i}',i) for i in range(100)]"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", script, db, p], cwd=".")
        for p in ("a", "b")
    ]
    codes = [p.wait(timeout=30) for p in procs]
    assert codes == [0, 0], codes
    m = MemoryCore(db)
    assert m.get_fact("bot", "k", "a99")["value"] == 99
    assert m.get_fact("bot", "k", "b99")["value"] == 99


def test_wal_is_enabled(tmp_path):
    db = str(tmp_path / "wal.db")
    core = MemoryCore(db)
    conn = core._get_connection()
    mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_reinit_on_existing_wal_db(tmp_path):
    db = str(tmp_path / "reinit.db")
    MemoryCore(db).add_fact("bot1", "user", "name", "Alice")
    core2 = MemoryCore(db)
    fact = core2.get_fact("bot1", "user", "name")
    assert fact is not None
    assert fact["value"] == "Alice"


def test_recent_events_isolated_by_agent(core):
    core.log_event("bot1", "ping", "from bot1", {"n": 1})
    core.log_event("bot2", "ping", "from bot2", {"n": 2})
    bot1_events = core.get_recent_events(limit=10, agent_id="bot1")
    assert len(bot1_events) == 1
    assert bot1_events[0]["agent_id"] == "bot1"
    assert bot1_events[0]["summary"] == "from bot1"
    assert len(core.get_recent_events(limit=10)) == 2


def test_recent_events_unknown_agent_returns_empty(core):
    core.log_event("bot1", "ping", "hello")
    assert core.get_recent_events(limit=10, agent_id="ghost") == []


def test_event_causal_link(core):
    root = core.log_event("bot1", "decision", "chose SQLite", {"why": "no orm"})
    child = core.log_event(
        "bot1", "outcome", "tests passed", caused_by=root, relation="enabled"
    )
    events = core.get_recent_events(limit=10, agent_id="bot1")
    child_row = next(e for e in events if e["id"] == child)
    assert child_row["parent_event_id"] == root
    assert child_row["relation"] == "enabled"


def test_trace_cause_chain(core):
    a = core.log_event("bot1", "decision", "A")
    b = core.log_event("bot1", "decision", "B", caused_by=a)
    c = core.log_event("bot1", "outcome", "C", caused_by=b)
    chain = core.trace_cause(c)
    assert [e["id"] for e in chain] == [c, b, a]
    assert chain[-1]["parent_event_id"] is None


def test_trace_cause_unknown_returns_empty(core):
    assert core.trace_cause(9999) == []


def test_log_event_rejects_bad_relation(core):
    with pytest.raises(ValueError):
        core.log_event("bot1", "x", "y", relation="bogus")


def test_log_event_rejects_missing_parent(core):
    with pytest.raises(ValueError):
        core.log_event("bot1", "x", "y", caused_by=9999)


def test_fact_as_of_returns_historical_value(core):
    core.add_fact("bot1", "user", "name", "Alice")
    first = core.get_fact("bot1", "user", "name")
    t1 = first["updated_at"]
    core.add_fact("bot1", "user", "name", "Bob")
    second = core.get_fact("bot1", "user", "name")
    t2 = second["updated_at"]
    # в момент t1 (до перезаписи) — Alice
    old = core.get_fact_as_of("bot1", "user", "name", t1)
    assert old is not None
    assert old["value"] == "Alice"
    # в момент t2 — Bob (текущая таблица)
    now = core.get_fact_as_of("bot1", "user", "name", t2)
    assert now is not None
    assert now["value"] == "Bob"


def test_fact_as_of_before_creation_returns_none(core):
    core.add_fact("bot1", "user", "name", "Alice")
    assert core.get_fact_as_of(
        "bot1", "user", "name", "2000-01-01T00:00:00+00:00"
    ) is None


def test_fact_history_records_versions(core):
    core.add_fact("bot1", "user", "name", "Alice")
    core.add_fact("bot1", "user", "name", "Bob")
    core.add_fact("bot1", "user", "name", "Carol")
    history = core.get_fact_history("bot1", "user", "name")
    assert [h["value"] for h in history] == ["Alice", "Bob"]


def test_fact_as_of_unknown_key_returns_none(core):
    assert core.get_fact_as_of(
        "bot1", "user", "ghost", "2030-01-01T00:00:00+00:00"
    ) is None


def test_value_type_symmetry_int(core):
    core.add_fact("bot1", "user", "age", 42)
    assert core.get_fact("bot1", "user", "age")["value"] == 42


def test_value_type_symmetry_str(core):
    core.add_fact("bot1", "user", "name", "Alice")
    assert core.get_fact("bot1", "user", "name")["value"] == "Alice"


def test_value_type_symmetry_numeric_string(core):
    # строка "42" должна остаться строкой, а не превратиться в число
    core.add_fact("bot1", "user", "code", "42")
    assert core.get_fact("bot1", "user", "code")["value"] == "42"


def test_value_type_symmetry_dict(core):
    payload = {"x": 1, "y": [2, 3]}
    core.add_fact("bot1", "meta", "cfg", payload)
    assert core.get_fact("bot1", "meta", "cfg")["value"] == payload


def test_value_type_symmetry_history(core):
    core.add_fact("bot1", "user", "age", 41)
    core.add_fact("bot1", "user", "age", 42)
    history = core.get_fact_history("bot1", "user", "age")
    assert history[0]["value"] == 41
    assert isinstance(history[0]["value"], int)


def test_legacy_plain_string_is_readable(core, tmp_path):
    # имитируем старую запись без маркера — должна читаться как строка
    import sqlite3
    db = str(tmp_path / "legacy.db")
    m = MemoryCore(db)
    conn = m._get_connection()
    now = "2026-01-01T00:00:00+00:00"
    with conn:
        conn.execute(
            "INSERT INTO facts (agent_id, category, key, value, confidence, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("bot1", "user", "old", "legacy-value", 1.0, now, now),
        )
    fact = m.get_fact("bot1", "user", "old")
    assert fact["value"] == "legacy-value"
    assert isinstance(fact["value"], str)
