"""Postgres connections come from a pool (warm, bounded); without psycopg_pool each query connects as before."""
import sys
import types
from contextlib import contextmanager

import pytest

from webapp import database as db


class FakeConn:
    def __init__(self):
        self.committed = self.closed = False

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


@pytest.fixture
def pg(monkeypatch):
    monkeypatch.setattr(db, "IS_PG", True)
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql://pool-test/db")
    monkeypatch.setattr(db, "_POOL", None)
    monkeypatch.setattr(db, "dict_row", object(), raising=False)
    monkeypatch.setattr(db.atexit, "register", lambda fn: fn)
    return monkeypatch


def test_queries_borrow_a_warm_connection_from_one_bounded_pool(pg):
    made, lent = [], []

    class ConnectionPool:
        check_connection = staticmethod(lambda conn: None)

        def __init__(self, conninfo, **kwargs):
            made.append((conninfo, kwargs))

        def close(self):
            pass

        @contextmanager
        def connection(self):
            conn = FakeConn(); lent.append(conn)
            yield conn

    pg.setitem(sys.modules, "psycopg_pool", types.SimpleNamespace(ConnectionPool=ConnectionPool))
    pg.setenv("DB_POOL_MAX", "7")
    for _ in range(3):
        with db._conn() as conn:
            assert isinstance(conn, FakeConn)
    assert len(made) == 1 and len(lent) == 3, "one pool for the process, a connection per query"
    conninfo, kwargs = made[0]
    assert conninfo == "postgresql://pool-test/db" and kwargs["max_size"] == 7 and kwargs["check"] is not None
    assert kwargs["kwargs"]["connect_timeout"] == 10


def test_without_the_pool_library_each_query_connects_and_commits_as_before(pg):
    pg.setitem(sys.modules, "psycopg_pool", None)            # import fails
    opened = []
    pg.setattr(db, "psycopg", types.SimpleNamespace(connect=lambda *a, **k: opened.append(FakeConn()) or opened[-1]),
               raising=False)
    with db._conn():
        pass
    assert db._POOL is False and len(opened) == 1 and opened[0].committed and opened[0].closed
