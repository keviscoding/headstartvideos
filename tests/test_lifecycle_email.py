"""Setup and release emails: once each, never after unsubscribing, the nudge only for new customers still not set up."""
import time

import pytest
from fastapi.testclient import TestClient

from webapp import database as db
from webapp import lifecycle_email as le
from webapp import milestones


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "IS_PG", False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "mail.db")
    monkeypatch.setattr(le, "_ready", set())
    monkeypatch.setattr(milestones, "_ready", set())
    monkeypatch.setenv("FRONTIER_SECRETS_KEY", "mail-test-secret")
    sent = []

    def send(user, kind, subject, page, plain):
        sent.append((user["id"], kind, subject, page, plain))
        return True
    monkeypatch.setattr(le, "_send", send)
    monkeypatch.setattr(le, "_later", lambda fn, *a, **k: fn(*a, **k))
    users = {1: {"id": 1, "email": "studio@example.com", "plan": "studio_pro"},
             2: {"id": 2, "email": "starter@example.com", "plan": "starter"}}
    monkeypatch.setattr(db, "get_user_by_id", lambda uid: users.get(int(uid)))
    return sent, users


def test_a_studio_subscription_gets_one_welcome_and_a_classic_one_none(env):
    sent, users = env
    le.on_subscribed(1); le.on_subscribed(1); le.on_subscribed(2)
    assert [(u, k) for u, k, *_ in sent] == [(1, "welcome")]
    _, _, subject, page, plain = sent[0]
    assert subject == "Your Studio is ready to download" and "channelrecipe.com/app#local-studio" in page
    assert "/email/unsubscribe?u=1&amp;t=" in page and "Unsubscribe from these emails:" in plain


def test_nobody_is_mailed_after_unsubscribing_and_a_forged_link_does_nothing(env):
    sent, users = env
    assert not le.opt_out(1, "forged")
    assert le.opt_out(1, le.unsubscribe_token(1))
    le.on_subscribed(1)
    assert sent == []


def test_the_install_nudge_reaches_only_new_customers_whose_studio_never_called_in(env):
    sent, users = env
    now = time.time()
    milestones.record(1, le.SUBSCRIBED, now - 26 * 3600)
    assert le.due_install_nudges(now) == [1]
    milestones.record(1, milestones.CONNECTED, now - 3600)
    assert le.due_install_nudges(now) == [], "connected: nothing to nudge"
    users[3] = {"id": 3, "email": "old@example.com", "plan": "studio_pro"}
    milestones.record(3, le.SUBSCRIBED, now - 9 * 86400)
    assert 3 not in le.due_install_nudges(now), "a week later it is too late to be helpful"
    users[4] = {"id": 4, "email": "new@example.com", "plan": "studio_max"}
    milestones.record(4, le.SUBSCRIBED, now - 30 * 3600)
    milestones.record(4, milestones.DOWNLOADED, now - 29 * 3600)
    assert le.send_due(now) == 1 and le.send_due(now) == 0, "downloaded but not connected: nudged once"
    assert sent[-1][1] == "install_nudge"


def test_the_nudge_waits_a_day_after_any_other_email(env):
    sent, users = env
    now = time.time()
    le.welcome(users[1])
    milestones.record(1, le.SUBSCRIBED, now - 26 * 3600)
    assert le.send_due(now) == 0, "the welcome went out less than 20 hours ago"


def test_only_the_first_finished_video_gets_an_email(env, monkeypatch):
    sent, users = env
    counts = iter([1, 2])
    monkeypatch.setattr(milestones, "of", lambda uid: {"finished": next(counts)})
    le.on_video_finished(users[1], "The Killer Mountain")
    le.on_video_finished(users[1], "Another one")
    assert [(k, s) for _, k, s, *_ in sent] == [("first_video", "Your first video is done")]
    assert "The Killer Mountain" in sent[0][3]


def test_a_failed_send_can_be_tried_again(env, monkeypatch):
    sent, users = env
    monkeypatch.setattr(le, "_send", lambda *a: (_ for _ in ()).throw(RuntimeError("provider down")))
    assert not le.welcome(users[1])
    monkeypatch.setattr(le, "_send", lambda user, kind, *a: sent.append((user["id"], kind)) or True)
    assert le.welcome(users[1]) and sent == [(1, "welcome")]


def test_release_notes_go_once_per_build_to_studio_customers(env, monkeypatch):
    sent, users = env
    with db._conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, email TEXT, plan TEXT)")
        conn.execute("INSERT INTO users VALUES (1,'studio@example.com','studio_pro'),(2,'starter@example.com','starter')")
    entry = {"build": 62, "title": "Studio tells you when your video is ready",
             "items": ["New: The tab shows progress.", "Fixed: Footage search on a slow connection."]}
    assert le.send_release(entry, pause_s=0) == {"sent": 1, "skipped": 0, "build": 62}
    assert le.send_release(entry, pause_s=0)["sent"] == 0, "the same build is never sent twice"
    _, kind, subject, page, _ = sent[0]
    assert kind == "release:62" and subject.startswith("Studio 62 is out") and "Update now" in page and "New:" not in page


def test_the_unsubscribe_link_works_from_the_site(env):
    from webapp import server
    c = TestClient(server.app)
    good = c.get(f"/email/unsubscribe?u=1&t={le.unsubscribe_token(1)}")
    assert good.status_code == 200 and "unsubscribed" in good.text and le.opted_out(1)
    assert c.post("/email/unsubscribe?u=2&t=nope").status_code == 400
