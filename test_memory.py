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
    assert a.get_fact("bot", "k", "a49")["value"] == "49"
    assert a.get_fact("bot", "k", "b49")["value"] == "49"


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
    assert m.get_fact("bot", "k", "a99")["value"] == "99"
    assert m.get_fact("bot", "k", "b99")["value"] == "99"


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
