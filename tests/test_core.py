from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init()


def test_password_hash_roundtrip():
    from app.security import hash_password, verify_password
    h = hash_password("correct horse battery")
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")


def test_rate_limiter():
    from app.security import RateLimiter
    rl = RateLimiter(limit=2, window=60)
    assert not rl.blocked("ip")
    rl.hit("ip"); rl.hit("ip")
    assert rl.blocked("ip")
    rl.reset("ip")
    assert not rl.blocked("ip")


def test_parse_dt_jellyfin_format():
    from app.clients import parse_dt
    dt = parse_dt("2026-11-01T12:30:45.1234567Z")
    assert dt == datetime(2026, 11, 1, 12, 30, 45, 123456, tzinfo=timezone.utc)
    assert parse_dt("2026-11-01").date() == date(2026, 11, 1)
    assert parse_dt(None) is None


def test_maintainerr_pending_deletions():
    from app.clients import Maintainerr
    added = "2026-10-01T10:00:00.000Z"
    cols = [
        {"id": 1, "isActive": True, "deleteAfterDays": 30, "arrAction": 0, "mediaServerType": "jellyfin",
         "media": [{"mediaServerId": "AAA", "addDate": added}, {"mediaServerId": "bbb", "addDate": added}]},
        {"id": 2, "isActive": True, "deleteAfterDays": 10, "arrAction": 1,
         "media": [{"mediaServerId": "bbb", "addDate": added}]},
        {"id": 3, "isActive": True, "deleteAfterDays": 5, "arrAction": 4,  # do nothing
         "media": [{"mediaServerId": "ccc", "addDate": added}]},
        {"id": 4, "isActive": False, "deleteAfterDays": 5, "arrAction": 0,
         "media": [{"mediaServerId": "ddd", "addDate": added}]},
        {"id": 5, "isActive": True, "deleteAfterDays": None, "arrAction": 0,
         "media": [{"mediaServerId": "eee", "addDate": added}]},
    ]
    m = Maintainerr("http://maintainerr")
    m.client = httpx.Client(base_url="http://maintainerr",
                            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=cols)))
    result = m.pending_deletions(ZoneInfo("Europe/Berlin"))
    assert set(result) == {"aaa", "bbb"}
    assert result["aaa"].delete_on == date(2026, 10, 31)
    assert result["bbb"].delete_on == date(2026, 10, 11)  # earliest wins


def test_render_and_message():
    from app import digest, mailer
    from app.clients import PendingDeletion

    class FakeJF:
        def items_by_ids(self, user, ids):
            return [{"Id": "aaa", "Name": "Paper <Comets>", "Type": "Movie", "ProductionYear": 2020}]

        def recently_added(self, user, types, since, limit=300):
            if types == "Episode":
                return [{"Id": "e1", "Type": "Episode", "SeriesId": "s1", "SeriesName": "Harbor",
                         "DateCreated": datetime.now(timezone.utc).isoformat()}]
            return []

    settings = {"language": "en", "server_name": "Home", "jellyfin_public_url": "https://jf.example.com",
                "smtp_from_email": "a@example.com", "smtp_from_name": "LC"}
    tz = ZoneInfo("Europe/Berlin")
    soon = datetime.now(tz).date() + timedelta(days=3)
    ctx = digest.Context(settings, FakeJF(), "srv", {"aaa": PendingDeletion("aaa", soon, "c")}, tz)
    d = digest.build_digest(ctx, "u", "Anna", datetime.now(timezone.utc) - timedelta(days=7))
    assert [c.title for c in d.leaving] == ["Paper <Comets>"]
    assert d.new[0].subtitle == "1 new episode"
    html, text = digest.render(d, ctx, lambda c: f"cid:{digest.cid_for(c)}")
    assert "Paper &lt;Comets&gt;" in html and "<Comets>" not in html
    assert "https://jf.example.com/web/#/details?id=aaa&amp;serverId=srv" in html
    assert "Leaving soon" in html and "Recently added" in html
    assert "Paper <Comets>" in text
    msg = mailer.build_message(settings, "anna@example.com", digest.subject(d, settings), html, text,
                               {"poster-aaa@lastcall": b"\xff\xd8jpeg"})
    types = [p.get_content_type() for p in msg.walk()]
    assert types == ["multipart/alternative", "text/plain", "multipart/related", "text/html", "image/jpeg"]


def test_empty_digest():
    from app.digest import Digest
    assert Digest("u", "n").empty
