"""Fake Jellyfin + Maintainerr API with made-up titles and generated posters.

Development/screenshot helper only, not part of the container image.

    python scripts/demo_server.py            # listens on 127.0.0.1:8899

Point Last Call at http://127.0.0.1:8899 for both Jellyfin and Maintainerr.
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


def poster_png(item_id: str, width: int = 400) -> bytes:
    item = ITEMS.get(item_id, {"Name": "?", "ProductionYear": ""})
    rnd = random.Random(item_id)
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
            return self.send_json(maintainerr_collections())
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


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    print(f"demo API on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
