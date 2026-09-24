"""Startup schema repair in the Jayti Hub (server/jayti-hub/jayti_hub_server.py).

Production incident: the hub connects as a role that doesn't own tables created
by postgres, so every ``ALTER TABLE`` fails. When the schema was sent as one
multi-statement string, Postgres ran it as a single implicit transaction and
that ALTER failure rolled back ``CREATE TABLE audit_trail`` too. These tests pin
the per-statement behaviour: CREATEs land even when ALTERs are denied, and
``_ensure_schema`` never raises.
"""
import asyncio
import importlib.util
import sys
import types
import warnings
from pathlib import Path

import pytest

HUB_PATH = Path(__file__).resolve().parent.parent / "server" / "jayti-hub" / "jayti_hub_server.py"


@pytest.fixture(scope="module")
def hub():
    """Load the hub by file path (its dir name has a hyphen, so it isn't importable).

    asyncpg/argon2 are stubbed only if they aren't installed, and the stubs are
    removed from sys.modules once the module is loaded so nothing else sees them.
    """
    stubs = {
        "asyncpg": {"Pool": object, "create_pool": None},
        "argon2": {"PasswordHasher": lambda *a, **k: object()},
    }
    with pytest.MonkeyPatch.context() as mp:
        for name, attrs in stubs.items():
            if name not in sys.modules and importlib.util.find_spec(name) is None:
                stub = types.ModuleType(name)
                stub.__dict__.update(attrs)
                mp.setitem(sys.modules, name, stub)
        spec = importlib.util.spec_from_file_location("jayti_hub_server_under_test", HUB_PATH)
        mod = importlib.util.module_from_spec(spec)
        with warnings.catch_warnings():  # the hub's @app.on_event is deprecated upstream
            warnings.simplefilter("ignore", DeprecationWarning)
            spec.loader.exec_module(mod)
    return mod


class DeniedAlterConn:
    """Fake connection where the role may CREATE but not ALTER.

    An execute() whose SQL contains an ALTER raises and commits nothing, which
    is what Postgres does to a multi-statement string (one implicit transaction).
    """

    def __init__(self):
        self.settings = []
        self.attempted = []
        self.committed = []

    async def execute(self, sql):
        if sql.startswith("SET "):
            self.settings.append(sql)
            return "SET"
        self.attempted.append(sql)
        if "ALTER TABLE" in sql:
            raise PermissionError("must be owner of table device_registry")
        self.committed.append(sql)
        return "CREATE TABLE"


class _Acquire:
    def __init__(self, conn, enter_error=None):
        self._conn = conn
        self._enter_error = enter_error

    async def __aenter__(self):
        if self._enter_error:
            raise self._enter_error
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn=None, acquire_error=None, enter_error=None):
        self.conn = conn
        self.acquire_error = acquire_error
        self.enter_error = enter_error

    def acquire(self):
        if self.acquire_error:
            raise self.acquire_error
        return _Acquire(self.conn, self.enter_error)


def _creates(statements):
    return [s for s in statements if s.lstrip().startswith("CREATE TABLE")]


def _alters(statements):
    return [s for s in statements if s.lstrip().startswith("ALTER TABLE")]


def test_statements_are_single_and_creates_come_first(hub):
    stmts = hub._SCHEMA_STATEMENTS
    assert all(";" not in s for s in stmts), "each entry must be one statement"
    n_create = len(_creates(stmts))
    assert n_create >= 1 and len(_alters(stmts)) >= 1
    assert _creates(stmts) == list(stmts[:n_create])
    assert len(_creates(stmts)) + len(_alters(stmts)) == len(stmts)


def test_referenced_table_is_created_before_api_keys(hub):
    # api_keys has a FK to device_registry; the reverse order fails on a fresh DB.
    def create_index(table):
        return next(i for i, s in enumerate(hub._SCHEMA_STATEMENTS)
                    if s.startswith(f"CREATE TABLE IF NOT EXISTS {table} "))

    assert create_index("device_registry") < create_index("api_keys")


def test_every_statement_attempted_when_alters_fail(hub):
    conn = DeniedAlterConn()
    asyncio.run(hub._ensure_schema(FakePool(conn)))  # must not raise
    assert conn.attempted == list(hub._SCHEMA_STATEMENTS)
    assert conn.settings == ["SET lock_timeout = '2s'"]


def test_audit_trail_created_even_though_alters_are_denied(hub):
    conn = DeniedAlterConn()
    asyncio.run(hub._ensure_schema(FakePool(conn)))
    assert conn.committed == _creates(hub._SCHEMA_STATEMENTS)
    assert any("CREATE TABLE IF NOT EXISTS audit_trail" in s for s in conn.committed)


def test_creates_run_before_and_after_a_failing_alter(hub, monkeypatch):
    stmts = hub._SCHEMA_STATEMENTS
    creates, alters = _creates(stmts), _alters(stmts)
    interleaved = (alters[0], creates[0], alters[1], creates[1], alters[2])
    monkeypatch.setattr(hub, "_SCHEMA_STATEMENTS", interleaved)

    conn = DeniedAlterConn()
    asyncio.run(hub._ensure_schema(FakePool(conn)))
    assert conn.attempted == list(interleaved)
    assert conn.committed == [creates[0], creates[1]]


def test_failures_logged_as_one_summary_line(hub, capsys):
    asyncio.run(hub._ensure_schema(FakePool(DeniedAlterConn())))
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    n_alter, n_total = len(_alters(hub._SCHEMA_STATEMENTS)), len(hub._SCHEMA_STATEMENTS)
    assert f"{n_alter} of {n_total} statements failed" in lines[0]
    assert "PermissionError: must be owner of table device_registry" in lines[0]


def test_no_output_when_everything_succeeds(hub, capsys):
    class OkConn:
        async def execute(self, sql):
            return "OK"

    asyncio.run(hub._ensure_schema(FakePool(OkConn())))
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("where", ["acquire", "aenter"])
def test_acquire_failure_is_swallowed(hub, capsys, where):
    err = ConnectionRefusedError("pool is closed")
    pool = FakePool(acquire_error=err) if where == "acquire" else FakePool(enter_error=err)
    asyncio.run(hub._ensure_schema(pool))  # must not raise
    assert "ConnectionRefusedError: pool is closed" in capsys.readouterr().out
