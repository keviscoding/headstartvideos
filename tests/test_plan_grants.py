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
    db._apply_pending_plan_grants()                                   # no account yet: nothing, retried next boot
    user = db.create_user("creator@example.com")
    db.update_user(user["id"], credits=3)
    db._apply_pending_plan_grants()
    got = db.get_user_by_id(user["id"])
    assert got["plan"] == "studio_max" and got["credits"] == 100, "Max, with its monthly credits"
    db.update_user(user["id"], plan="daily", credits=7)                # later changes stand: applied once
    db._apply_pending_plan_grants()
    got = db.get_user_by_id(user["id"])
    assert got["plan"] == "daily" and got["credits"] == 7
