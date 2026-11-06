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
        {"id": 2, "isActive": True, "deleteAfterDays": 10, "arrAction": 1, "mediaServerType": "jellyfin",
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
        key, product, label = "jellyfin", "Jellyfin", "Home"

        def link(self, item_id):
            return f"https://jf.example.com/web/#/details?id={item_id}&serverId=srv"

        def home_url(self):
            return "https://jf.example.com/web/"

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
    jf = FakeJF()
    ctx = digest.Context(settings, {"jellyfin": jf}, {"jellyfin": {"aaa": PendingDeletion("aaa", soon, "c")}}, tz)
    d = digest.build_digest(ctx, [(jf, "u")], "1", "Anna", datetime.now(timezone.utc) - timedelta(days=7))
    assert [c.title for c in d.leaving] == ["Paper <Comets>"]
    assert d.new[0].subtitle == "1 new episode"
    html, text = digest.render(d, ctx, lambda c: f"cid:{digest.cid_for(c)}")
    assert "Paper &lt;Comets&gt;" in html and "<Comets>" not in html
    assert "https://jf.example.com/web/#/details?id=aaa&amp;serverId=srv" in html
    assert "Leaving soon" in html and "Recently added" in html
    assert "Paper <Comets>" in text
    msg = mailer.build_message(settings, "anna@example.com", digest.subject(d, ctx), html, text,
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
    """Fake media server backed by a static library."""

    def __init__(self, items, episodes=(), key="jellyfin"):
        self.key, self.product, self.label = key, key.capitalize(), f"{key} home"
        self.items, self.episodes = items, list(episodes)

    def _tag(self, items):
        return [{**i, "_src": self.key} for i in items]

    def link(self, item_id):
        return f"https://{self.key}.example.com/{item_id}"

    def home_url(self):
        return f"https://{self.key}.example.com/"

    def close(self):
        pass

    def library(self, user):
        return self._tag(i for i in self.items if i["Type"] in ("Movie", "Series"))

    def items_by_ids(self, user, ids):
        wanted = set(ids)
        return self._tag(i for i in self.items if i["Id"] in wanted)

    def recently_added(self, user, types, since, limit=300):
        pool = self.episodes if types == "Episode" else [i for i in self.items if i["Type"] in types.split(",")]
        from app.clients import parse_dt
        out = self._tag(i for i in pool if parse_dt(i["DateCreated"]) >= since)
        return sorted(out, key=lambda i: i["DateCreated"], reverse=True)


def make_ctx(jf, deletions=(), extra=None, **settings):
    """Context with `jf` (and optional `extra` = (source, deletions)) as media servers."""
    from app import digest
    from app.clients import PendingDeletion
    tz = ZoneInfo("Europe/Berlin")
    base = {"language": "en", "reco_enabled": "1", "reco_count": "6", "rewatch_enabled": "1",
            "rewatch_count": "4", "rewatch_min_days": "180", "suggestion_cooldown_days": "90"}
    base.update(settings)
    soon = datetime.now(tz).date() + timedelta(days=5)
    sources = {jf.key: jf}
    dels = {jf.key: {d: PendingDeletion(d, soon, "c") for d in deletions}}
    if extra:
        src, extra_dels = extra
        sources[src.key] = src
        dels[src.key] = {d: PendingDeletion(d, soon, "c") for d in extra_dels}
    return digest.Context(base, sources, dels, tz)


def build(ctx, since_days=7):
    from app import digest
    accounts = [(src, "u1") for src in ctx.sources.values()]
    return digest.build_digest(ctx, accounts, "1", "Anna", NOW - timedelta(days=since_days))


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


@pytest.fixture
def fixed_day(monkeypatch):
    """Make the daily random seed independent of today's date."""
    from app import digest
    state = {"day": "2026-01-01"}
    monkeypatch.setattr(digest, "daily_seed", lambda pid, tz: f"{pid}:{state['day']}")
    return state


def test_recommendations_match_taste_and_skip_watched(fixed_day):
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


def test_suggestion_cooldown(fixed_day):
    from app import db
    items = [movie(f"m{i}", f"Movie {i}", str(100 + i), 500) for i in range(3)]
    db.record_suggestions("1", {"Movie:tmdb:100", "Movie:tmdb:101"})
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
    db.sync_accounts("jellyfin", [{"id": "u1", "name": "Anna"}])
    person = db.list_people()[0]
    db.update_person(person["id"], "anna@example.com", True, "both", "u1", None)
    assert digest.send_to(ctx, db.get_person(person["id"])) == "sent"
    later = digest.build_digest(ctx, [(jf, "u1")], str(person["id"]), "Anna", NOW)  # nothing is new any more
    assert "Brand New" not in [c.title for c in later.reco]


# --------------------------------------------------------------------------
# Plex and combined mails
# --------------------------------------------------------------------------

def test_plex_normalize():
    from app.plex import normalize
    m = {"ratingKey": "42", "type": "movie", "title": "Neon Harbor", "year": 2021, "addedAt": 1700000000,
         "viewCount": 2, "lastViewedAt": 1690000000, "userRating": 8.0, "audienceRating": 7.1,
         "Genre": [{"tag": "Thriller"}], "Guid": [{"id": "tmdb://123"}, {"id": "imdb://tt42"}], "thumb": "/t"}
    it = normalize(m)
    assert (it["Id"], it["Type"], it["_src"]) == ("42", "Movie", "plex")
    assert it["ProviderIds"] == {"Tmdb": "123", "Imdb": "tt42"}
    assert it["UserData"]["Played"] and it["UserData"]["PlayCount"] == 2 and it["UserData"]["IsFavorite"]
    show = normalize({"ratingKey": 7, "type": "show", "title": "S", "leafCount": 10, "viewedLeafCount": 4})
    assert show["Type"] == "Series" and show["UserData"]["PlayedPercentage"] == 40 and not show["UserData"]["Played"]
    ep = normalize({"ratingKey": 9, "type": "episode", "title": "E", "grandparentTitle": "S",
                    "grandparentRatingKey": 7, "parentIndex": 2, "index": 5})
    assert (ep["SeriesId"], ep["SeriesName"], ep["ParentIndexNumber"], ep["IndexNumber"]) == ("7", "S", 2, 5)


def test_plex_link_and_token_redaction():
    from app.plex import Plex, _redact
    p = Plex("http://plex:32400", "secret")
    p.machine_id = "abc"
    assert p.link("42") == "https://app.plex.tv/desktop/#!/server/abc/details?key=%2Flibrary%2Fmetadata%2F42"
    p.public_url = "https://plex.example.com"
    assert p.link("42").startswith("https://plex.example.com/web/index.html#!/server/abc/")
    assert "secret" not in _redact("GET http://plex/x?X-Plex-Token=secret&a=1")
    p.close()


def test_maintainerr_split_by_server():
    from app.clients import Maintainerr
    added = "2026-10-01T10:00:00.000Z"
    cols = [
        {"id": 1, "isActive": True, "deleteAfterDays": 30, "arrAction": 0, "mediaServerType": "jellyfin",
         "media": [{"mediaServerId": "jf1", "addDate": added}]},
        {"id": 2, "isActive": True, "deleteAfterDays": 30, "arrAction": 0, "mediaServerType": "plex",
         "media": [{"mediaServerId": "101", "addDate": added}]},
        {"id": 3, "isActive": True, "deleteAfterDays": 30, "arrAction": 0,  # old Maintainerr = Plex
         "media": [{"plexId": 102, "addDate": added}]},
    ]
    m = Maintainerr("http://maintainerr")
    m.client = httpx.Client(base_url="http://maintainerr",
                            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=cols)))
    tz = ZoneInfo("Europe/Berlin")
    assert set(m.pending_deletions(tz, "jellyfin")) == {"jf1"}
    assert set(m.pending_deletions(tz, "plex")) == {"101", "102"}


def test_combined_mail_deduplicates_across_servers():
    jf = LibJF([movie("j1", "Neon Harbor", "1", 400), movie("j2", "Velvet Static", "2", 1),
                movie("j3", "Only On Jellyfin", "3", 400)])
    px = LibJF([movie("p1", "Neon Harbor", "1", 400), movie("p2", "Velvet Static", "2", 1),
                movie("p3", "Glass Orchard", "4", 400)], key="plex")
    # Neon Harbor leaves Plex but stays on Jellyfin; Glass Orchard leaves Plex for good
    ctx = make_ctx(jf, extra=(px, ["p1", "p3"]), reco_enabled="0", rewatch_enabled="0")
    d = build(ctx)
    assert [c.title for c in d.leaving] == ["Glass Orchard"] and d.leaving[0].src == "plex"
    assert [c.title for c in d.new] == ["Velvet Static"]
    from app import digest
    html, text = digest.render(d, ctx, lambda c: None)
    assert "Plex" in html and "[Plex]" in text  # server label on each card in combined mails
    assert "Open jellyfin home" in html and "Open plex home" in html


def test_watched_on_other_server_is_not_recommended():
    jf = LibJF([movie("j1", "Seen On Jellyfin", "1", 400, Played=True, PlayCount=1, LastPlayedDate=_iso(20))])
    px = LibJF([movie("p1", "Seen On Jellyfin", "1", 400), movie("p2", "Fresh", "2", 400)], key="plex")
    d = build(make_ctx(jf, extra=(px, []), rewatch_enabled="0"))
    assert [c.title for c in d.reco] == ["Fresh"]


def test_accounts_for_choices():
    from app import digest
    jf, px = LibJF([]), LibJF([], key="plex")
    ctx = make_ctx(jf, extra=(px, []))
    person = {"servers": "both", "jellyfin_id": "j", "plex_id": "p"}
    assert [s.key for s, _ in digest.accounts_for(person, ctx)[0]] == ["jellyfin", "plex"]
    assert [s.key for s, _ in digest.accounts_for({**person, "servers": "plex"}, ctx)[0]] == ["plex"]
    pairs, problems = digest.accounts_for({**person, "servers": "jellyfin", "jellyfin_id": None}, ctx)
    assert not pairs and problems
    del ctx.sources["plex"]
    ctx.errors["plex"] = "down"
    pairs, problems = digest.accounts_for(person, ctx)
    assert [s.key for s, _ in pairs] == ["jellyfin"] and problems == ["down"]


def test_sync_accounts_links_people_using_both_servers():
    from app import db
    db.sync_accounts("jellyfin", [{"id": "j-anna", "name": "Anna"}, {"id": "j-ben", "name": "Ben"}])
    added, linked, removed = db.sync_accounts("plex", [
        {"id": "owner", "name": "anna", "email": "Anna@Example.com"},
        {"id": "77", "name": "carla", "email": "carla@example.com"},
    ])
    assert (added, linked, removed) == (1, 1, 0)
    people = {p["name"]: p for p in db.list_people()}
    assert people["Anna"]["plex_id"] == "owner" and people["Anna"]["email"] == "anna@example.com"
    assert people["carla"]["jellyfin_id"] is None and not people["carla"]["enabled"]
    # re-import is idempotent: no duplicates
    assert db.sync_accounts("plex", [{"id": "owner", "name": "anna"}, {"id": "77", "name": "carla"}]) == (0, 0, 0)
    assert len(db.list_people()) == 3
    # an account that disappears is unlinked; a person without accounts is removed
    db.sync_accounts("plex", [{"id": "owner", "name": "anna"}])
    assert {p["name"] for p in db.list_people()} == {"Anna", "Ben"}


def test_migration_from_v1(tmp_path, monkeypatch):
    import sqlite3
    from app import db
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE recipients (jellyfin_id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL DEFAULT '',
                                 enabled INTEGER NOT NULL DEFAULT 0, last_sent TEXT);
        CREATE TABLE suggestions (user_id TEXT NOT NULL, item_key TEXT NOT NULL, ts TEXT NOT NULL,
                                  PRIMARY KEY (user_id, item_key));
        INSERT INTO recipients VALUES ('jf-1', 'Anna', 'anna@example.com', 1, '2026-11-01T10:00:00+00:00');
        INSERT INTO suggestions VALUES ('jf-1', 'Movie:tmdb:1', '2026-11-01T10:00:00+00:00');
    """)
    con.commit()
    con.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init()
    db.init()  # idempotent
    people = db.list_people()
    assert len(people) == 1
    p = people[0]
    assert (p["name"], p["email"], p["enabled"], p["jellyfin_id"], p["servers"]) == \
        ("Anna", "anna@example.com", 1, "jf-1", "both")
    with db.connect() as c:
        assert c.execute("SELECT user_id FROM suggestions").fetchone()[0] == str(p["id"])


def test_recommendations_prefer_taste_on_every_day(fixed_day):
    """Weighted randomness must not let unrelated genres win on unlucky days."""
    items = [movie("w1", "Seen Thriller", "30", 500, genres=["Thriller"], Played=True, PlayCount=1,
                   LastPlayedDate=_iso(10))]
    items += [movie(f"t{i}", f"Thriller {i}", f"3{i}1", 500, genres=["Thriller"]) for i in range(3)]
    items += [movie(f"r{i}", f"Romance {i}", f"3{i}2", 500, genres=["Romance"], rating=6.0) for i in range(3)]
    worst = 3
    for day in range(365):
        fixed_day["day"] = (date(2026, 1, 1) + timedelta(days=day)).isoformat()
        d = build(make_ctx(LibJF(items), reco_count="3", rewatch_enabled="0"))
        worst = min(worst, sum(c.title.startswith("Thriller") for c in d.reco))
    assert worst >= 2
