"""Settings that stop two web containers double-counting (2026-10-26).

- Every process shares the managed database's ~22 connections: DB_POOL_MAX per web container, and each Fly cook or
  niche machine gets DB_POOL_MAX=2 (not the web's own value). Idle connections close after a minute.
  prepare_threshold=None, so a connection pooler (PgBouncer) can sit in front later.
- Table creation and migrations take an advisory lock, one key per module: both containers boot together on a deploy.
  On Postgres an existing column is never ALTERed again (an ACCESS EXCLUSIVE lock on users at every boot).
Checked against a real Postgres 16 too (three processes booting a fresh database at once, then again): no errors.
"""
import ast
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

from webapp import database as db
from webapp import fly_bridge

WEBAPP = Path(db.__file__).resolve().parent


@pytest.fixture
def pool(monkeypatch):
    made = []

    class ConnectionPool:
        check_connection = staticmethod(lambda conn: None)

        def __init__(self, conninfo, **kwargs):
            made.append(kwargs)

        def close(self):
            pass

    monkeypatch.setattr(db, "IS_PG", True)
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql://pool-test/db")
    monkeypatch.setattr(db, "_POOL", None)
    monkeypatch.setattr(db, "dict_row", object(), raising=False)
    monkeypatch.setattr(db.atexit, "register", lambda fn: fn)
    monkeypatch.setitem(sys.modules, "psycopg_pool", types.SimpleNamespace(ConnectionPool=ConnectionPool))

    def make():
        monkeypatch.setattr(db, "_POOL", None)
        db._pool()
        return made[-1]
    return make


@pytest.mark.parametrize("env,size", [(None, 12), ("6", 6), ("2", 2), ("lots", 12), ("0", 1)])
def test_the_pool_honours_db_pool_max(pool, monkeypatch, env, size):
    if env is None:
        monkeypatch.delenv("DB_POOL_MAX", raising=False)
    else:
        monkeypatch.setenv("DB_POOL_MAX", env)
    monkeypatch.delenv("DB_POOL_MIN", raising=False)
    kwargs = pool()
    assert kwargs["max_size"] == size and kwargs["min_size"] <= size


def test_idle_connections_close_after_a_minute_and_nothing_is_prepared_server_side(pool, monkeypatch):
    kwargs = pool()
    assert kwargs["max_idle"] == 60
    assert kwargs["kwargs"]["prepare_threshold"] is None and kwargs["kwargs"]["connect_timeout"] == 10


def test_direct_connections_prepare_nothing_either(monkeypatch):
    seen = []

    class Conn:
        def commit(self):
            pass

        def close(self):
            pass
    monkeypatch.setattr(db, "IS_PG", True)
    monkeypatch.setattr(db, "_POOL", False)
    monkeypatch.setattr(db, "dict_row", object(), raising=False)
    monkeypatch.setattr(db, "psycopg", types.SimpleNamespace(connect=lambda *a, **k: seen.append(k) or Conn()),
                        raising=False)
    with db._conn():
        pass
    assert seen[0]["prepare_threshold"] is None


def test_fly_cooks_get_a_small_pool_not_the_web_containers(monkeypatch):
    monkeypatch.setenv("DB_POOL_MAX", "6")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/db")
    env = fly_bridge._machine_env()
    assert env["DB_POOL_MAX"] == "2" and "DB_POOL_MAX" not in fly_bridge._INJECT_ENV_KEYS


def test_fly_niche_machines_get_a_small_pool_too(monkeypatch):
    sent = []
    monkeypatch.setattr(fly_bridge.config, "COOK_ON_FLY", True, raising=False)
    monkeypatch.setattr(fly_bridge.config, "FLY_COOK_APP", "cr-cook", raising=False)
    monkeypatch.setattr(fly_bridge.config, "FLY_NICHE_IMAGE", "registry.fly.io/cr-niche:1", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/db")
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt-test")
    monkeypatch.setenv("DB_POOL_MAX", "6")
    monkeypatch.setattr(fly_bridge, "_request", lambda method, path, body=None: sent.append(body) or {"id": "m1"})
    assert fly_bridge.spawn_niche_scrape_machine("niche-job") == "m1"
    assert sent[0]["config"]["env"]["DB_POOL_MAX"] == "2"


class _Cursor:
    """A psycopg cursor or connection stand-in: execute returns a result whose fetchone answers the catalog."""

    def __init__(self, columns):
        self.columns, self.sql = columns, []

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        self.last = params
        return self

    def fetchone(self):
        if self.sql[-1][0].startswith("SELECT data_type"):
            return self.columns.get(self.last[1])
        return None


def test_on_postgres_an_existing_column_is_never_altered_again(monkeypatch):
    monkeypatch.setattr(db, "IS_PG", True)
    cur = _Cursor({"trial_used": {"data_type": "integer", "column_default": "0"}})
    db._ensure_column(cur, "users", "trial_used", "INTEGER NOT NULL DEFAULT 0")
    assert not any(sql.startswith("ALTER") for sql, _ in cur.sql), "no ACCESS EXCLUSIVE lock on users at boot"
    db._ensure_column(cur, "users", "period_tier", "TEXT DEFAULT ''")
    assert cur.sql[-1][0] == "ALTER TABLE users ADD COLUMN IF NOT EXISTS period_tier TEXT DEFAULT ''"


def test_schema_locks_are_one_key_per_module(monkeypatch):
    monkeypatch.setattr(db, "IS_PG", True)
    a, b = _Cursor({}), _Cursor({})
    db.schema_lock(a, "core")
    db.schema_lock(b, "cloud_jobs")
    (sql_a, key_a), (sql_b, key_b) = a.sql[0], b.sql[0]
    assert sql_a == sql_b == f"SELECT pg_advisory_xact_lock({db.SCHEMA_LOCK}, %s::int)"
    assert key_a != key_b and all(0 <= k[0] < 2 ** 31 for k in (key_a, key_b))
    monkeypatch.setattr(db, "IS_PG", False)
    c = _Cursor({})
    db.schema_lock(c)
    assert c.sql == [], "SQLite: one process, nothing to lock"


def _functions_creating_tables(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            src = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
            own = [n for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and "CREATE TABLE IF NOT EXISTS" in n.value]
            if own and not any(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node
                               for child in ast.walk(node)):
                yield node.name, src


# Tables made on the side, once per process, small and additive: no lock needed (named, so a new one is a choice).
UNLOCKED_ON_PURPOSE = {("database.py", "_apply_pending_plan_grants")}


@pytest.mark.parametrize("name", sorted(p.name for p in WEBAPP.glob("*.py")))
def test_every_table_creation_takes_the_schema_lock_first(name):
    path = WEBAPP / name
    if "CREATE TABLE IF NOT EXISTS" not in path.read_text(encoding="utf-8"):
        return
    missing = [fn for fn, src in _functions_creating_tables(path)
               if "schema_lock(" not in src and "pg_advisory_xact_lock" not in src
               and (name, fn) not in UNLOCKED_ON_PURPOSE]
    assert not missing, f"{name}: {missing} create tables without database.schema_lock(conn, name)"
