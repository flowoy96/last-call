"""Fake Jellyfin, Plex, plex.tv and Maintainerr APIs with made-up titles and generated posters.

Development/screenshot helper only, not part of the container image.

    python scripts/demo_server.py        # Jellyfin + Maintainerr on :8899, Plex + plex.tv on :8898

Point Last Call at http://127.0.0.1:8899 for Jellyfin and Maintainerr (for both servers),
and at http://127.0.0.1:8898 with token "demo-plex-token" for Plex. Start Last Call with
LASTCALL_PLEX_TV_URL=http://127.0.0.1:8898 so plex.tv requests go to the fake as well.
"""

from __future__ import annotations

import colorsys
import hashlib
import io
import json
import random
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw, ImageFilter, ImageFont

NOW = datetime.now(timezone.utc)


def _id(name: str) -> str:
    return hashlib.md5(name.encode()).hexdigest()


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


# (name, type, year, library, days since added, genres, community rating)
CATALOG = [
    ("The Lighthouse Keeper's Daughter", "Movie", 2019, "movies", 400, ["Drama", "Mystery"], 7.4),
    ("Neon Harbor", "Movie", 2021, "movies", 380, ["Thriller", "Crime"], 6.9),
    ("Glass Orchard", "Movie", 2017, "movies", 500, ["Drama"], 7.8),
    ("Paper Comets", "Movie", 2020, "movies", 450, ["Comedy", "Romance"], 6.5),
    ("Midnight at Copper Lake", "Movie", 2018, "movies", 610, ["Thriller", "Mystery"], 7.1),
    ("The Quiet Cartographer", "Movie", 2022, "movies", 300, ["Adventure", "Drama"], 7.6),
    ("Saltwater Kingdom", "Series", 2016, "shows", 700, ["Drama", "Fantasy"], 8.1),
    ("Orbit of Small Things", "Series", 2019, "shows", 520, ["Sci-Fi", "Comedy"], 7.3),
    ("Velvet Static", "Movie", 2024, "movies", 2, ["Sci-Fi", "Thriller"], 7.0),
    ("Northbound Echo", "Movie", 2025, "movies", 3, ["Adventure"], 6.8),
    ("The Clockmaker's Garden", "Movie", 2023, "movies", 5, ["Fantasy", "Drama"], 7.2),
    ("Ember & Tide", "Series", 2025, "shows", 1, ["Drama", "Mystery"], 7.9),
    ("Little Robot, Big Sky", "Movie", 2024, "kids", 4, ["Animation", "Family"], 7.5),
    ("Dragons of Pebble Hill", "Movie", 2021, "kids", 200, ["Animation", "Fantasy"], 6.9),
    ("Harbor Lights", "Series", 2022, "shows", 400, ["Crime", "Drama"], 8.0),
    ("Silent Meridian", "Movie", 2016, "movies", 900, ["Thriller", "Mystery"], 7.7),
    ("The Fox and the Lantern", "Movie", 2015, "movies", 1000, ["Fantasy", "Adventure"], 7.0),
    ("Concrete Lullaby", "Movie", 2019, "movies", 640, ["Crime", "Drama"], 7.3),
    ("Static Bloom", "Movie", 2020, "movies", 560, ["Sci-Fi", "Drama"], 6.6),
    ("A Map of Rain", "Movie", 2014, "movies", 1100, ["Romance", "Drama"], 6.9),
    ("Kettle Island", "Series", 2018, "shows", 820, ["Mystery", "Crime"], 7.8),
    ("Tin Crown", "Series", 2021, "shows", 430, ["Fantasy", "Adventure"], 7.1),
    ("Low Tide Detectives", "Series", 2020, "shows", 600, ["Crime", "Comedy"], 7.4),
]

ITEMS = {}


def add_item(name, kind, year, lib, age, genres, rating, suffix=""):
    iid = _id(name + suffix)
    ITEMS[iid] = {
        "Id": iid, "Name": name, "Type": kind, "ProductionYear": year,
        "DateCreated": iso(NOW - timedelta(days=age, hours=3)), "_lib": lib,
        "Genres": genres, "CommunityRating": rating,
        "ProviderIds": {"Tmdb": str(int(_id(name)[:6], 16))},
        "ImageTags": {"Primary": "x"},
    }
    return iid


for row in CATALOG:
    add_item(*row)

# Duplicates in a separate 4K library (different item ids, same TMDb id):
#  - Neon Harbor: 1080p copy is scheduled for deletion, 4K copy stays -> not "leaving"
#  - Velvet Static: both copies are new -> listed once
#  - The Quiet Cartographer: old title, 4K copy added yesterday -> not "new"
add_item("Neon Harbor", "Movie", 2021, "movies4k", 90, ["Thriller", "Crime"], 6.9, suffix="#4k")
add_item("Velvet Static", "Movie", 2024, "movies4k", 2, ["Sci-Fi", "Thriller"], 7.0, suffix="#4k")
add_item("The Quiet Cartographer", "Movie", 2022, "movies4k", 1, ["Adventure", "Drama"], 7.6, suffix="#4k")

# Three new episodes for an existing series; episode 5 exists in two qualities.
_harbor = _id("Harbor Lights")
for n, version in ((4, ""), (5, ""), (5, " 2160p"), (6, "")):
    eid = _id(f"Harbor Lights S02E{n:02d}{version}")
    ITEMS[eid] = {
        "Id": eid, "Name": f"Episode {n}", "Type": "Episode", "SeriesId": _harbor,
        "SeriesName": "Harbor Lights", "ParentIndexNumber": 2, "IndexNumber": n,
        "DateCreated": iso(NOW - timedelta(days=max(n - 3, 0), hours=1)), "_lib": "shows", "ImageTags": {},
    }

USERS = [
    {"Id": _id("user-anna"), "Name": "Anna", "Policy": {"IsDisabled": False},
     "_libs": {"movies", "movies4k", "shows", "kids"}},
    {"Id": _id("user-ben"), "Name": "Ben", "Policy": {"IsDisabled": False}, "_libs": {"movies", "shows"}},
    {"Id": _id("user-kids"), "Name": "Kids", "Policy": {"IsDisabled": False}, "_libs": {"kids"}},
]


def _played(days_ago, fav=False, count=1):
    return {"Played": True, "PlayCount": count, "IsFavorite": fav,
            "LastPlayedDate": iso(NOW - timedelta(days=days_ago))}


# Watch history per user: title -> UserData
USER_DATA = {
    "Anna": {
        "Silent Meridian": _played(420, fav=True, count=2),
        "Midnight at Copper Lake": _played(300),
        "Concrete Lullaby": _played(510),
        "The Quiet Cartographer": _played(20),          # too recent for "watch again"
        "The Fox and the Lantern": _played(700),
        "Kettle Island": {"Played": False, "PlayedPercentage": 40.0, "UnplayedItemCount": 6},
    },
    "Ben": {
        "Paper Comets": _played(400),
        "A Map of Rain": _played(800, fav=True),
        "Tin Crown": {"Played": True, "PlayCount": 1},
    },
}

# Maintainerr: two collections with titles scheduled for removal
LEAVING = [
    ("The Lighthouse Keeper's Daughter", 26), ("Neon Harbor", 27), ("Glass Orchard", 24),
    ("Paper Comets", 29), ("Dragons of Pebble Hill", 22),
]
LEAVING_SHOWS = [("Saltwater Kingdom", 55), ("Orbit of Small Things", 50)]


def maintainerr_collections() -> list[dict]:
    def media(title, days_ago, cid):
        return {"collectionId": cid, "mediaServerId": _id(title), "addDate": iso(NOW - timedelta(days=days_ago))}
    return [
        {"id": 1, "title": "Old & unwatched movies", "isActive": True, "deleteAfterDays": 30,
         "arrAction": 0, "mediaServerType": "jellyfin", "type": "movie",
         "media": [media(t, d, 1) for t, d in LEAVING]},
        {"id": 2, "title": "Abandoned shows", "isActive": True, "deleteAfterDays": 60,
         "arrAction": 1, "mediaServerType": "jellyfin", "type": "show",
         "media": [media(t, d, 2) for t, d in LEAVING_SHOWS]},
        {"id": 3, "title": "Just watching", "isActive": True, "deleteAfterDays": None,
         "arrAction": 4, "mediaServerType": "jellyfin", "type": "movie",
         "media": [media("Harbor Lights", 3, 3)]},
    ]


def poster_png(item_id: str, width: int = 400, item: dict | None = None) -> bytes:
    item = item or ITEMS.get(item_id, {"Name": "?", "ProductionYear": ""})
    rnd = random.Random(item.get("Name", item_id))
    h = rnd.random()
    w, ht = width, int(width * 1.5)
    top = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, 0.65, 0.55))
    bottom = tuple(int(c * 255) for c in colorsys.hsv_to_rgb((h + 0.12) % 1, 0.8, 0.12))
    img = Image.new("RGB", (w, ht))
    d = ImageDraw.Draw(img)
    for y in range(ht):
        f = y / ht
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] * (1 - f) + bottom[i] * f) for i in range(3)))
    # abstract shapes
    shapes = Image.new("RGBA", (w, ht), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shapes)
    for _ in range(6):
        r = rnd.randint(w // 8, w // 2)
        x, y = rnd.randint(-r, w), rnd.randint(0, int(ht * 0.7))
        col = tuple(int(c * 255) for c in colorsys.hsv_to_rgb((h + rnd.random() * 0.3) % 1, 0.5, 1)) + (rnd.randint(40, 110),)
        sd.ellipse([x, y, x + r, y + r], fill=col)
    img.paste(shapes.filter(ImageFilter.GaussianBlur(w // 40)), (0, 0), shapes.filter(ImageFilter.GaussianBlur(w // 40)))
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=w // 10)
    small = ImageFont.load_default(size=w // 18)
    words, lines, line = item["Name"].upper().split(), [], ""
    for word in words:
        test = f"{line} {word}".strip()
        if d.textlength(test, font=font) > w * 0.86 and line:
            lines.append(line)
            line = word
        else:
            line = test
    lines.append(line)
    y = int(ht * 0.66)
    for ln in lines:
        d.text((w * 0.07, y), ln, font=font, fill=(255, 255, 255))
        y += int(w / 9)
    d.text((w * 0.07, y + 6), str(item.get("ProductionYear", "")), font=small, fill=(220, 220, 220))
    d.rectangle([0, 0, w, 8], fill=(225, 29, 72))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def public(item: dict, user: dict | None = None) -> dict:
    out = {k: v for k, v in item.items() if not k.startswith("_")}
    if user is not None:
        ud = {"Played": False, "PlayCount": 0, "IsFavorite": False, "PlaybackPositionTicks": 0, "Key": item["Id"]}
        ud.update(USER_DATA.get(user["Name"], {}).get(item["Name"] if item["Type"] != "Episode" else "", {}))
        out["UserData"] = ud
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = {k.lower(): v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        if p == "/api/collections/overlay-data":
            return self.send_json(maintainerr_collections() + plex_maintainerr_collections())
        if p == "/System/Info":
            return self.send_json({"Id": "demo-server-id", "ServerName": "Demo Jellyfin", "Version": "10.11.0"})
        if p == "/Users":
            return self.send_json([{k: v for k, v in u.items() if not k.startswith("_")} for u in USERS])
        if p == "/Items":
            user = next((x for x in USERS if x["Id"] == q.get("userid")), None)
            libs = user["_libs"] if user else set()
            items = [i for i in ITEMS.values() if i["_lib"] in libs]
            if "ids" in q:
                ids = {x.replace("-", "").lower() for x in q["ids"].split(",")}
                items = [i for i in items if i["Id"] in ids]
            if "includeitemtypes" in q:
                types = set(q["includeitemtypes"].split(","))
                items = [i for i in items if i["Type"] in types]
            if q.get("sortby") == "DateCreated":
                items.sort(key=lambda i: i["DateCreated"], reverse=q.get("sortorder") == "Descending")
            start, limit = int(q.get("startindex", 0)), int(q.get("limit", 1000))
            return self.send_json({"Items": [public(i, user) for i in items[start:start + limit]], "TotalRecordCount": len(items)})
        if p.startswith("/Items/") and p.endswith("/Images/Primary"):
            iid = p.split("/")[2]
            if iid not in ITEMS:
                return self.send_json({}, 404)
            body = poster_png(iid, min(int(q.get("maxwidth", 400)), 600))
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_json({"error": "not found"}, 404)


# --------------------------------------------------------------------------
# Plex
# --------------------------------------------------------------------------

PLEX_MACHINE = "demo-plex-machine"
PLEX_TOKENS = {"demo-plex-token": "anna", "tok-carla": "carla", "tok-kids": "kids"}
PLEX_LIBS = {"anna": {"1", "2"}, "carla": {"1"}, "kids": {"1"}}
# Plex Home: "Kids" is a managed user without e-mail, "Grandma" is protected by a PIN
HOME_USERS_XML = ('<?xml version="1.0" encoding="UTF-8"?><MediaContainer>'
                  '<User id="1" title="anna" admin="1" email="anna@example.com"/>'
                  '<User id="501" title="Kids" email="" restricted="1" protected="0" home="1"/>'
                  '<User id="502" title="Grandma" email="" protected="1" home="1"/></MediaContainer>')

# (title, type, year, section, days since added) - TMDb ids match the Jellyfin catalog by title
PLEX_CATALOG = [
    ("Neon Harbor", "movie", 2021, "1", 380),            # leaving on Plex, stays on Jellyfin
    ("Glass Orchard", "movie", 2017, "1", 500),          # leaving on both servers -> listed once
    ("Velvet Static", "movie", 2024, "1", 2),            # new on both -> listed once
    ("Copper Skies", "movie", 2025, "1", 1),             # new, Plex only
    ("Midnight at Copper Lake", "movie", 2018, "1", 610),
    ("Silent Meridian", "movie", 2016, "1", 900),
    ("Paper Lanterns at Dawn", "movie", 2019, "1", 640),  # Plex only
    ("Wild Signal", "show", 2023, "2", 300),             # Plex only
    ("Saltwater Kingdom", "show", 2016, "2", 700),
]
GENRES = {row[0]: row[5] for row in CATALOG}
GENRES.update({"Copper Skies": ["Sci-Fi", "Adventure"], "Paper Lanterns at Dawn": ["Romance", "Drama"],
               "Wild Signal": ["Thriller", "Sci-Fi"]})
PLEX_ITEMS = {}
for n, (title, kind, year, sec, age) in enumerate(PLEX_CATALOG, start=101):
    PLEX_ITEMS[str(n)] = {
        "ratingKey": str(n), "key": f"/library/metadata/{n}", "type": kind, "title": title, "year": year,
        "addedAt": int((NOW - timedelta(days=age)).timestamp()), "librarySectionID": sec,
        "Genre": [{"tag": g} for g in GENRES.get(title, ["Drama"])], "audienceRating": 7.0,
        "Guid": [{"id": f"tmdb://{int(_id(title)[:6], 16)}"}], "thumb": f"/library/metadata/{n}/thumb/1",
        **({"leafCount": 10, "viewedLeafCount": 0} if kind == "show" else {}),
    }
PLEX_VIEWS = {  # per Plex user: title -> (viewCount, days since last view, userRating)
    "anna": {"Midnight at Copper Lake": (1, 300, 0), "Paper Lanterns at Dawn": (2, 500, 9)},
    "carla": {"Silent Meridian": (1, 400, 0)},
}


def plex_maintainerr_collections() -> list[dict]:
    by_title = {m["title"]: k for k, m in PLEX_ITEMS.items()}
    media = [{"collectionId": 10, "mediaServerId": by_title[t], "addDate": iso(NOW - timedelta(days=d))}
             for t, d in (("Neon Harbor", 25), ("Glass Orchard", 24), ("Midnight at Copper Lake", 23))]
    return [{"id": 10, "title": "Plex: old movies", "isActive": True, "deleteAfterDays": 30, "arrAction": 0,
             "mediaServerType": "plex", "type": "movie", "media": media}]


def plex_view(item: dict, user: str) -> dict:
    out = dict(item)
    views, days, rating = PLEX_VIEWS.get(user, {}).get(item["title"], (0, 0, 0))
    if views:
        out.update(viewCount=views, lastViewedAt=int((NOW - timedelta(days=days)).timestamp()))
    if rating:
        out["userRating"] = rating
    return out


class PlexHandler(Handler):
    def send_xml(self, text: str, status: int = 200):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        p = urlparse(self.path).path
        if p == "/api/home/users/501/switch":
            return self.send_xml('<user id="501" authenticationToken="kids-auth"/>')
        if p == "/api/home/users/502/switch":
            return self.send_json({"error": "PIN required"}, 401)
        self.send_json({"error": "not found"}, 404)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        # plex.tv
        if p == "/api/home/users":
            return self.send_xml(HOME_USERS_XML)
        if p == "/api/v2/resources" and self.headers.get("X-Plex-Token") == "kids-auth":
            return self.send_json([{"name": "Living Room Plex", "clientIdentifier": PLEX_MACHINE,
                                    "accessToken": "tok-kids"}])
        if p == "/api/v2/user":
            return self.send_json({"username": "anna", "email": "anna@example.com", "title": "Anna"})
        if p == f"/api/servers/{PLEX_MACHINE}/shared_servers":
            body = ('<?xml version="1.0" encoding="UTF-8"?><MediaContainer>'
                    '<SharedServer id="1" username="carla" email="carla@example.com" userID="77" '
                    'accessToken="tok-carla"><Section key="1" title="Movies" type="movie" shared="1"/></SharedServer>'
                    '</MediaContainer>').encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        user = PLEX_TOKENS.get(q.get("X-Plex-Token", ""))
        if not user:
            return self.send_json({"error": "unauthorized"}, 401)
        libs = PLEX_LIBS[user]
        if p == "/":
            return self.send_json({"MediaContainer": {"friendlyName": "Living Room Plex", "version": "1.41.0",
                                                      "machineIdentifier": PLEX_MACHINE}})
        if p == "/library/sections":
            dirs = [{"key": "1", "type": "movie", "title": "Movies"}, {"key": "2", "type": "show", "title": "TV"}]
            return self.send_json({"MediaContainer": {"Directory": [d for d in dirs if d["key"] in libs]}})
        if p.startswith("/library/sections/") and p.endswith("/all"):
            sec = p.split("/")[3]
            if sec not in libs:
                return self.send_json({}, 404)
            want = {"1": "movie", "2": "show", "4": "episode"}.get(q.get("type", ""), "")
            items = [plex_view(m, user) for m in PLEX_ITEMS.values() if m["librarySectionID"] == sec and m["type"] == want]
            if "addedAt>>" in q:
                items = [m for m in items if m["addedAt"] >= int(q["addedAt>>"])]
            start, size = int(q.get("X-Plex-Container-Start", 0)), int(q.get("X-Plex-Container-Size", 1000))
            return self.send_json({"MediaContainer": {"Metadata": items[start:start + size]}})
        if p.startswith("/library/metadata/"):
            keys = p.split("/")[3].split(",")
            items = [plex_view(PLEX_ITEMS[k], user) for k in keys
                     if k in PLEX_ITEMS and PLEX_ITEMS[k]["librarySectionID"] in libs]
            if not items:
                return self.send_json({}, 404)
            return self.send_json({"MediaContainer": {"Metadata": items}})
        if p == "/photo/:/transcode":
            key = q.get("url", "").split("/")[3] if q.get("url", "").count("/") >= 3 else ""
            m = PLEX_ITEMS.get(key)
            if not m:
                return self.send_json({}, 404)
            body = poster_png(key, min(int(q.get("width", 400)), 600),
                              {"Name": m["title"], "ProductionYear": m["year"]})
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    import sys
    import threading
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    plex_port = int(sys.argv[2]) if len(sys.argv) > 2 else 8898
    plex = ThreadingHTTPServer(("127.0.0.1", plex_port), PlexHandler)
    threading.Thread(target=plex.serve_forever, daemon=True).start()
    print(f"demo Jellyfin/Maintainerr API on http://127.0.0.1:{port}, Plex + plex.tv on http://127.0.0.1:{plex_port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
