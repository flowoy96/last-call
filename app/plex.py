"""Plex Media Server client with the same interface as clients.Jellyfin.

Plex items are normalised into the Jellyfin-style dicts the digest code uses
(Id, Name, Type, ProviderIds, Genres, UserData, ...).

Per-user data: plex.tv hands out an access token per shared user and server
(`/api/servers/{machineId}/shared_servers`). Queries made with that token only
see the libraries shared with the user and return *their* watch state, the
same way Jellyfin's `userId` parameter does. The owner uses the admin token.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from .clients import TIMEOUT, USER_AGENT, ApiError, _clean

PLEX_TV = os.environ.get("LASTCALL_PLEX_TV_URL", "https://plex.tv").rstrip("/")
OWNER = "owner"

TYPES = {"movie": "Movie", "show": "Series", "season": "Season", "episode": "Episode"}
SEARCH_TYPES = {"Movie": 1, "Series": 2, "Episode": 4}
SECTION_FOR = {"Movie": "movie", "Series": "show", "Episode": "show"}
GUID_PROVIDERS = {"tmdb": "Tmdb", "imdb": "Imdb", "tvdb": "Tvdb"}


def _iso(epoch: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def normalize(m: dict) -> dict:
    """Plex metadata -> Jellyfin-style item dict."""
    kind = TYPES.get(m.get("type", ""), m.get("type", ""))
    item: dict[str, Any] = {
        "Id": str(m.get("ratingKey")),
        "Name": m.get("title", ""),
        "Type": kind,
        "ProductionYear": m.get("year") or m.get("parentYear"),
        "DateCreated": _iso(m.get("addedAt")),
        "Genres": [g.get("tag") for g in m.get("Genre") or [] if g.get("tag")],
        "CommunityRating": m.get("audienceRating") or m.get("rating"),
        "ImageTags": {"Primary": "x"} if m.get("thumb") else {},
        "_src": "plex",
    }
    providers = {}
    for g in m.get("Guid") or []:
        scheme, _, value = (g.get("id") or "").partition("://")
        if scheme in GUID_PROVIDERS and value:
            providers[GUID_PROVIDERS[scheme]] = value
    item["ProviderIds"] = providers
    if kind == "Season":
        item.update(SeriesName=m.get("parentTitle"), SeriesId=str(m.get("parentRatingKey")),
                    IndexNumber=m.get("index"))
    elif kind == "Episode":
        item.update(SeriesName=m.get("grandparentTitle"), SeriesId=str(m.get("grandparentRatingKey")),
                    ParentIndexNumber=m.get("parentIndex"), IndexNumber=m.get("index"))

    views = int(m.get("viewCount") or 0)
    ud: dict[str, Any] = {
        "PlayCount": views,
        "Played": views > 0,
        "LastPlayedDate": _iso(m.get("lastViewedAt")),
        "IsFavorite": float(m.get("userRating") or 0) >= 8,  # 4+ of 5 stars
        "PlaybackPositionTicks": int(m.get("viewOffset") or 0),
    }
    if kind == "Series":
        leaves, viewed = int(m.get("leafCount") or 0), int(m.get("viewedLeafCount") or 0)
        ud["Played"] = bool(leaves) and viewed >= leaves
        ud["PlayedPercentage"] = (viewed / leaves * 100) if leaves else 0
    item["UserData"] = ud
    return item


class Plex:
    key = "plex"
    product = "Plex"

    def __init__(self, url: str, token: str, public_url: str = "", label: str = ""):
        if not url or not token:
            raise ApiError("Plex URL and token must be configured")
        self.base = _clean(url)
        self.token = token
        self.public_url = _clean(public_url)
        self.label = label or "Plex"
        self.machine_id = ""
        self._user_tokens: dict[str, str] = {}
        self._sections: dict[str, list[dict]] = {}
        self.client = httpx.Client(base_url=self.base, timeout=TIMEOUT, headers=self._headers())

    def _headers(self) -> dict:
        return {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "X-Plex-Product": "Last Call",
            "X-Plex-Client-Identifier": "last-call",
        }

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- low level ---------------------------------------------------------

    def _token(self, user: str) -> str:
        if user in ("", OWNER):
            return self.token
        if not self._user_tokens:
            self._load_shared()
        tok = self._user_tokens.get(str(user))
        if not tok:
            raise ApiError(f"Plex user {user} has no access to this server (any more)")
        return tok

    def _get(self, path: str, user: str = OWNER, params: dict | None = None, ok404: bool = False) -> dict:
        p = dict(params or {})
        p["X-Plex-Token"] = self._token(user)
        try:
            r = self.client.get(path, params=p)
            if ok404 and r.status_code == 404:
                return {}
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ApiError(f"Plex request {path} failed: {_redact(str(e))}") from None
        return (r.json() or {}).get("MediaContainer", {})

    def _plex_tv(self, path: str) -> httpx.Response:
        try:
            r = httpx.get(f"{PLEX_TV}{path}", timeout=TIMEOUT,
                          headers={**self._headers(), "X-Plex-Token": self.token})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ApiError(f"plex.tv request {path} failed: {_redact(str(e))}") from None
        return r

    # --- server / users ----------------------------------------------------

    def connect(self) -> dict:
        info = self._get("/")
        self.machine_id = info.get("machineIdentifier", "")
        if not self.label or self.label == "Plex":
            self.label = info.get("friendlyName") or self.label
        return info

    def system_info(self) -> dict:
        info = self.connect()
        return {"ServerName": info.get("friendlyName", "?"), "Version": info.get("version", "")}

    def _load_shared(self) -> list[dict]:
        if not self.machine_id:
            self.connect()
        root = ET.fromstring(self._plex_tv(f"/api/servers/{self.machine_id}/shared_servers").content)
        shared = []
        for el in root.iter("SharedServer"):
            uid = el.get("userID")
            if not uid:
                continue
            if el.get("accessToken"):
                self._user_tokens[uid] = el.get("accessToken")
            shared.append({"id": uid, "name": el.get("username") or el.get("name") or el.get("email") or uid,
                           "email": el.get("email") or ""})
        return shared

    def accounts(self) -> list[dict]:
        """Owner plus every user this server is shared with (with e-mail from plex.tv)."""
        me = self._plex_tv("/api/v2/user").json()
        owner = {"id": OWNER, "name": me.get("username") or me.get("title") or "Owner", "email": me.get("email") or ""}
        return [owner] + self._load_shared()

    # --- items ---------------------------------------------------------------

    def _sections_for(self, user: str) -> list[dict]:
        if user not in self._sections:
            dirs = self._get("/library/sections", user).get("Directory", [])
            self._sections[user] = [d for d in dirs if d.get("type") in ("movie", "show")]
        return self._sections[user]

    def _search(self, user: str, kinds: list[str], extra: dict | None = None, limit: int = 100000) -> list[dict]:
        out: list[dict] = []
        for kind in kinds:
            for sec in self._sections_for(user):
                if sec.get("type") != SECTION_FOR[kind]:
                    continue
                start, page = 0, 500
                while len(out) < limit:
                    params = {"type": SEARCH_TYPES[kind], "includeGuids": 1,
                              "X-Plex-Container-Start": start, "X-Plex-Container-Size": page, **(extra or {})}
                    meta = self._get(f"/library/sections/{sec['key']}/all", user, params).get("Metadata", [])
                    out.extend(normalize(m) for m in meta)
                    if len(meta) < page:
                        break
                    start += page
        return out

    def library(self, user: str) -> list[dict]:
        return self._search(user, ["Movie", "Series"])

    def items_by_ids(self, user: str, ids: list[str]) -> list[dict]:
        out: list[dict] = []
        keys = [i for i in ids if str(i).isdigit()]
        for i in range(0, len(keys), 50):
            chunk = ",".join(keys[i : i + 50])
            meta = self._get(f"/library/metadata/{chunk}", user, {"includeGuids": 1}, ok404=True).get("Metadata", [])
            out.extend(normalize(m) for m in meta)
        return out

    def recently_added(self, user: str, types: str, since: datetime, limit: int = 300) -> list[dict]:
        kinds = [k for k in types.split(",") if k in SEARCH_TYPES]
        items = self._search(user, kinds, {"addedAt>>": int(since.timestamp()), "sort": "addedAt:desc"}, limit)
        items.sort(key=lambda i: i.get("DateCreated") or "", reverse=True)
        return items[:limit]

    def image(self, item_id: str, max_width: int = 400) -> bytes | None:
        if not str(item_id).isdigit():
            return None
        try:
            r = self.client.get("/photo/:/transcode", params={
                "url": f"/library/metadata/{item_id}/thumb", "width": max_width,
                "height": int(max_width * 1.5), "minSize": 1, "upscale": 1, "X-Plex-Token": self.token,
            })
        except httpx.HTTPError:
            return None
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            return None
        return r.content

    # --- links ---------------------------------------------------------------

    def _web(self) -> str:
        return f"{self.public_url}/web/index.html" if self.public_url else "https://app.plex.tv/desktop/"

    def link(self, item_id: str) -> str:
        key = quote(f"/library/metadata/{item_id}", safe="")
        return f"{self._web()}#!/server/{self.machine_id}/details?key={key}"

    def home_url(self) -> str:
        return f"{self._web()}#!/media/{self.machine_id}/com.plexapp.plugins.library" if self.machine_id else self._web()


def _redact(text: str) -> str:
    """Remove X-Plex-Token values from error messages (they end up in the log)."""
    import re
    return re.sub(r"(X-Plex-Token=)[^&\s'\"]+", r"\1***", text)
