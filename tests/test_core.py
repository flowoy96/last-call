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
        def library(self, user):
            return []

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


# --------------------------------------------------------------------------
# duplicates, recommendations, rewatch
# --------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


def movie(iid, name, tmdb, created_days, genres=("Drama",), rating=7.0, **ud):
    return {"Id": iid, "Name": name, "Type": "Movie", "ProductionYear": 2020, "DateCreated": _iso(created_days),
            "ProviderIds": {"Tmdb": tmdb}, "Genres": list(genres), "CommunityRating": rating,
            "UserData": {"Played": False, "PlayCount": 0, "IsFavorite": False, **ud}}


class LibJF:
    """Fake Jellyfin backed by a static library."""

    def __init__(self, items, episodes=()):
        self.items, self.episodes = items, list(episodes)

    def library(self, user):
        return [dict(i) for i in self.items if i["Type"] in ("Movie", "Series")]

    def items_by_ids(self, user, ids):
        wanted = set(ids)
        return [dict(i) for i in self.items if i["Id"] in wanted]

    def recently_added(self, user, types, since, limit=300):
        pool = self.episodes if types == "Episode" else [i for i in self.items if i["Type"] in types.split(",")]
        from app.clients import parse_dt
        out = [dict(i) for i in pool if parse_dt(i["DateCreated"]) >= since]
        return sorted(out, key=lambda i: i["DateCreated"], reverse=True)


def make_ctx(jf, deletions=(), **settings):
    from app import digest
    from app.clients import PendingDeletion
    tz = ZoneInfo("Europe/Berlin")
    base = {"language": "en", "reco_enabled": "1", "reco_count": "6", "rewatch_enabled": "1",
            "rewatch_count": "4", "rewatch_min_days": "180", "suggestion_cooldown_days": "90"}
    base.update(settings)
    soon = datetime.now(tz).date() + timedelta(days=5)
    dels = {d: PendingDeletion(d, soon, "c") for d in deletions}
    return digest.Context(base, jf, "srv", dels, tz)


def build(ctx, since_days=7):
    from app import digest
    return digest.build_digest(ctx, "u1", "Anna", NOW - timedelta(days=since_days))


def all_titles(d):
    return [c.title for c in d.leaving + d.new + d.reco + d.rewatch]


def test_leaving_skipped_when_other_version_stays():
    jf = LibJF([movie("a1080", "Neon Harbor", "1", 400), movie("a4k", "Neon Harbor", "1", 90),
                movie("b", "Glass Orchard", "2", 400)])
    d = build(make_ctx(jf, deletions=["a1080", "b"], reco_enabled="0", rewatch_enabled="0"))
    assert [c.title for c in d.leaving] == ["Glass Orchard"]


def test_leaving_listed_once_when_all_versions_go():
    jf = LibJF([movie("a1080", "Neon Harbor", "1", 400), movie("a4k", "Neon Harbor", "1", 90)])
    d = build(make_ctx(jf, deletions=["a1080", "a4k"], reco_enabled="0", rewatch_enabled="0"))
    assert [c.title for c in d.leaving] == ["Neon Harbor"]


def test_new_versions_deduplicated():
    jf = LibJF([
        movie("v1", "Velvet Static", "10", 2), movie("v2", "Velvet Static", "10", 1),   # both new -> once
        movie("q1", "Quiet Cartographer", "11", 300), movie("q2", "Quiet Cartographer", "11", 1),  # 4K of old
        movie("n1", "Northbound", "12", 3),
    ])
    d = build(make_ctx(jf, reco_enabled="0", rewatch_enabled="0"))
    assert sorted(c.title for c in d.new) == ["Northbound", "Velvet Static"]


def test_new_episodes_counted_once_per_episode():
    series = {"Id": "s1", "Name": "Harbor Lights", "Type": "Series", "DateCreated": _iso(400),
              "ProviderIds": {"Tvdb": "9"}, "UserData": {}}
    eps = [{"Id": f"e{i}", "Type": "Episode", "SeriesId": "s1", "SeriesName": "Harbor Lights",
            "ParentIndexNumber": 2, "IndexNumber": n, "Name": f"Ep {n}", "DateCreated": _iso(1)}
           for i, n in enumerate([4, 5, 5, 6])]
    d = build(make_ctx(LibJF([series], eps), reco_enabled="0", rewatch_enabled="0"))
    assert [(c.title, c.subtitle) for c in d.new] == [("Harbor Lights", "3 new episodes")]


def test_rewatch_only_long_ago_and_no_duplicates():
    jf = LibJF([
        movie("old", "Silent Meridian", "20", 900, Played=True, PlayCount=2, LastPlayedDate=_iso(400)),
        movie("old4k", "Silent Meridian", "20", 100),                       # other copy, same title
        movie("recent", "Quiet Cartographer", "21", 300, Played=True, PlayCount=1, LastPlayedDate=_iso(20)),
        movie("never", "Paper Comets", "22", 300),
    ])
    d = build(make_ctx(jf, reco_enabled="0"))
    assert [c.title for c in d.rewatch] == ["Silent Meridian"]
    assert d.rewatch[0].badge.startswith("Last seen")


def test_recommendations_match_taste_and_skip_watched():
    items = [movie("w1", "Seen Thriller", "30", 500, genres=["Thriller"], Played=True, PlayCount=1,
                   LastPlayedDate=_iso(10))]
    items += [movie(f"t{i}", f"Thriller {i}", f"3{i}1", 500, genres=["Thriller"]) for i in range(3)]
    items += [movie(f"r{i}", f"Romance {i}", f"3{i}2", 500, genres=["Romance"], rating=6.0) for i in range(3)]
    items.append({"Id": "s1", "Name": "Started Show", "Type": "Series", "DateCreated": _iso(500),
                  "Genres": ["Thriller"], "UserData": {"Played": False, "PlayedPercentage": 30.0}})
    items.append({"Id": "s2", "Name": "Fresh Show", "Type": "Series", "DateCreated": _iso(500),
                  "Genres": ["Thriller"], "UserData": {"Played": False}})
    d = build(make_ctx(LibJF(items), reco_count="4", rewatch_enabled="0"))
    titles = [c.title for c in d.reco]
    assert len(titles) == 4 and len(set(titles)) == 4
    assert "Seen Thriller" not in titles and "Started Show" not in titles
    assert "Fresh Show" in titles
    assert sum(t.startswith("Thriller") for t in titles) >= 2  # taste wins over random


def test_suggestion_cooldown():
    from app import db
    items = [movie(f"m{i}", f"Movie {i}", str(100 + i), 500) for i in range(3)]
    db.record_suggestions("u1", {"Movie:tmdb:100", "Movie:tmdb:101"})
    d = build(make_ctx(LibJF(items), reco_count="3", rewatch_enabled="0"))
    assert [c.title for c in d.reco] == ["Movie 2"]


def test_no_title_in_two_sections():
    jf = LibJF([
        movie("x", "Leaving Old Favourite", "40", 900, Played=True, PlayCount=1, LastPlayedDate=_iso(500)),
        movie("y", "Brand New", "41", 1),
    ])
    d = build(make_ctx(jf, deletions=["x"]))
    titles = all_titles(d)
    assert len(titles) == len(set(titles))
    assert [c.title for c in d.leaving] == ["Leaving Old Favourite"] and not d.rewatch


def test_should_send_rules():
    from app.digest import Card, Digest
    d = Digest("u", "n", reco=[Card("a", "a", "A")])
    assert not d.should_send({"send_suggestions_only": "0"})
    assert d.should_send({"send_suggestions_only": "1"})
    d.new = [Card("b", "b", "B")]
    assert d.should_send({"send_suggestions_only": "0"})


@pytest.mark.parametrize("freq,expected_days", [("weekly", 7), ("biweekly", 14), ("daily", 1)])
def test_schedule_frequencies(freq, expected_days):
    from app.scheduler import build_trigger
    trig = build_trigger({"schedule_frequency": freq, "schedule_weekday": "fri", "schedule_time": "18:00"})
    first = trig.get_next_fire_time(None, datetime.now(timezone.utc))
    second = trig.get_next_fire_time(first, first + timedelta(minutes=1))
    assert (second - first).days == expected_days
    assert first.hour == 18 and (freq == "daily" or first.weekday() == 4)


def test_schedule_monthly_first_weekday():
    from app.scheduler import build_trigger
    trig = build_trigger({"schedule_frequency": "monthly", "schedule_weekday": "mon", "schedule_time": "09:30"})
    first = trig.get_next_fire_time(None, datetime.now(timezone.utc))
    assert first.weekday() == 0 and first.day <= 7 and (first.hour, first.minute) == (9, 30)


def test_announced_new_titles_not_recommended_next_time(monkeypatch):
    from app import db, digest, mailer
    monkeypatch.setattr(mailer, "send", lambda *a, **k: None)
    monkeypatch.setattr(digest, "poster", lambda jf, pid: None)
    jf = LibJF([movie("n1", "Brand New", "50", 1), movie("o1", "Older", "51", 300)])
    ctx = make_ctx(jf, rewatch_enabled="0", reco_count="5")
    db.upsert_recipient_name("u1", "Anna")
    db.update_recipient("u1", "anna@example.com", True)
    assert digest.send_to(ctx, db.get_recipient("u1")) == "sent"
    later = digest.build_digest(ctx, "u1", "Anna", NOW)  # nothing is new any more
    assert "Brand New" not in [c.title for c in later.reco]
