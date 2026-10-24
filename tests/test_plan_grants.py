"""Plans the owner gives (database._PENDING_PLAN_GRANTS): applied once per grant key, retried until the account exists,
and the plan's monthly credits topped up, never lowered."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import webapp.database as database  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(database, "_PENDING_CREDIT_GRANTS", [])
    monkeypatch.setattr(database, "_PENDING_USER_NOTICES", [])
    monkeypatch.setattr(database, "_PENDING_PLAN_GRANTS", [("grant-max", "Creator@Example.com", "studio_max"),
                                                           ("grant-bad", "creator@example.com", "no_such_plan")])
    database._init_db()
    return database


def test_a_given_plan_waits_for_the_account_then_applies_once(db):
    db._apply_pending_plan_grants()                                   # no account yet: nothing, retried later
    with db._conn() as conn:                                          # an account made outside create_user
        conn.cursor().execute(db._q("INSERT INTO users (email, credits) VALUES (?, ?)"), ("creator@example.com", 3))
    user = db.get_user_by_email("creator@example.com")
    assert user["plan"] != "studio_max"
    db._apply_pending_plan_grants()                                   # the next boot
    got = db.get_user_by_id(user["id"])
    assert got["plan"] == "studio_max" and got["credits"] == 100, "Max, with its monthly credits"
    db.update_user(user["id"], plan="daily", credits=7)                # later changes stand: applied once
    db._apply_pending_plan_grants()
    got = db.get_user_by_id(user["id"])
    assert got["plan"] == "daily" and got["credits"] == 7


def test_a_plan_given_before_signup_applies_the_moment_they_sign_up(db):
    user = db.create_user("creator@example.com")                       # signing up, no restart in between
    assert user["plan"] == "studio_max" and user["credits"] == 100
    assert db.create_user("someone-else@example.com")["plan"] != "studio_max"
