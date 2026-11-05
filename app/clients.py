"""Minimal synchronous clients for the Jellyfin and Maintainerr HTTP APIs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import httpx

TIMEOUT = httpx.Timeout(20.0, connect=10.0)
USER_AGENT = "LastCall/1.0"

ITEM_FIELDS = "ProductionYear,DateCreated,ProviderIds,Genres"
LIBRARY_FIELDS = "ProductionYear,DateCreated,ProviderIds,Genres"


class ApiError(RuntimeError):
    pass


def _clean(url: str) -> str:
    return url.strip().rstrip("/")


# --------------------------------------------------------------------------
# Jellyfin
# --------------------------------------------------------------------------

class Jellyfin:
    def __init__(self, url: str, api_key: str):
        if not url or not api_key:
            raise ApiError("Jellyfin URL and API key must be configured")
        self.base = _clean(url)
        self.client = httpx.Client(
            base_url=self.base,
            timeout=TIMEOUT,
            headers={
                "Authorization": f'MediaBrowser Token="{api_key}", Client="Last Call"',
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get(self, path: str, **params: Any) -> Any:
        try:
            r = self.client.get(path, params={k: v for k, v in params.items() if v is not None})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ApiError(f"Jellyfin request {path} failed: {e}") from e
        return r.json()

    def system_info(self) -> dict:
        return self._get("/System/Info")

    def users(self) -> list[dict]:
        return [u for u in self._get("/Users") if not u.get("Policy", {}).get("IsDisabled")]

    def items_by_ids(self, user_id: str, ids: list[str]) -> list[dict]:
        """Items the given user can see. Jellyfin applies library access and
        parental-control rules for us when `userId` is passed."""
        out: list[dict] = []
        for i in range(0, len(ids), 50):
            chunk = ids[i : i + 50]
            data = self._get("/Items", userId=user_id, ids=",".join(chunk), fields=ITEM_FIELDS)
            out.extend(data.get("Items", []))
        return out

    def library(self, user_id: str) -> list[dict]:
        """All movies and series the user can access, with their watch state (UserData)."""
        out: list[dict] = []
        start, page = 0, 1000
        while True:
            data = self._get(
                "/Items",
                userId=user_id,
                includeItemTypes="Movie,Series",
                recursive="true",
                fields=LIBRARY_FIELDS,
                enableUserData="true",
                enableImages="false",
                excludeLocationTypes="Virtual",
                startIndex=start,
                limit=page,
            )
            items = data.get("Items", [])
            out.extend(items)
            if len(items) < page:
                return out
            start += page

    def recently_added(self, user_id: str, types: str, since: datetime, limit: int = 300) -> list[dict]:
        """Items of `types` created after `since`, newest first (paged, cut off client-side)."""
        out: list[dict] = []
        start, page = 0, 100
        while start < limit:
            data = self._get(
                "/Items",
                userId=user_id,
                includeItemTypes=types,
                recursive="true",
                sortBy="DateCreated",
                sortOrder="Descending",
                fields=ITEM_FIELDS,
                startIndex=start,
                limit=page,
                excludeLocationTypes="Virtual",
            )
            items = data.get("Items", [])
            for it in items:
                created = parse_dt(it.get("DateCreated"))
                if created is None or created < since:
                    return out
                out.append(it)
            if len(items) < page:
                break
            start += page
        return out

    def image(self, item_id: str, max_width: int = 400) -> bytes | None:
        try:
            r = self.client.get(
                f"/Items/{item_id}/Images/Primary",
                params={"maxWidth": max_width, "quality": 85},
            )
        except httpx.HTTPError:
            return None
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            return None
        return r.content


# --------------------------------------------------------------------------
# Maintainerr
# --------------------------------------------------------------------------

# ServarrAction enum in Maintainerr (packages/contracts/src/collections/servarr-action.ts)
ACTION_DELETE = 0
ACTION_UNMONITOR_DELETE_ALL = 1
ACTION_UNMONITOR_DELETE_EXISTING = 2
ACTION_UNMONITOR = 3
ACTION_DO_NOTHING = 4
ACTION_DELETE_SHOW_IF_EMPTY = 5
ACTION_UNMONITOR_SHOW_IF_EMPTY = 6
ACTION_CHANGE_QUALITY_PROFILE = 7

# Actions that remove media files from the library.
REMOVING_ACTIONS = {
    ACTION_DELETE,
    ACTION_UNMONITOR_DELETE_ALL,
    ACTION_UNMONITOR_DELETE_EXISTING,
    ACTION_DELETE_SHOW_IF_EMPTY,
}


@dataclass
class PendingDeletion:
    media_server_id: str
    delete_on: date
    collection: str


class Maintainerr:
    def __init__(self, url: str, api_key: str = ""):
        if not url:
            raise ApiError("Maintainerr URL must be configured")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if api_key:
            # Maintainerr itself has no API auth; the key is passed for reverse proxies.
            headers["X-Api-Key"] = api_key
        self.client = httpx.Client(base_url=_clean(url), timeout=TIMEOUT, headers=headers)

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get(self, path: str, **params: Any) -> httpx.Response:
        try:
            return self.client.get(path, params=params or None)
        except httpx.HTTPError as e:
            raise ApiError(f"Maintainerr request {path} failed: {e}") from e

    def collections_with_media(self) -> list[dict]:
        # Newer Maintainerr versions return the full media list in one call.
        r = self._get("/api/collections/overlay-data")
        if r.status_code == 200 and isinstance(r.json(), list):
            return r.json()
        # Fallback for older versions: list collections, then their media.
        r = self._get("/api/collections")
        if r.status_code != 200:
            raise ApiError(f"Maintainerr /api/collections returned HTTP {r.status_code}")
        collections = r.json() or []
        for col in collections:
            m = self._get("/api/collections/media/", collectionId=col["id"])
            col["media"] = m.json() if m.status_code == 200 else []
        return collections

    def pending_deletions(self, tz) -> dict[str, PendingDeletion]:
        """Map of media-server item id -> earliest planned removal date."""
        result: dict[str, PendingDeletion] = {}
        for col in self.collections_with_media():
            if not col.get("isActive", True):
                continue
            days = col.get("deleteAfterDays")
            if days is None or col.get("arrAction") not in REMOVING_ACTIONS:
                continue
            server_type = col.get("mediaServerType")
            if server_type and server_type != "jellyfin":
                continue
            for media in col.get("media") or []:
                media_id = media.get("mediaServerId") or media.get("plexId")
                added = parse_dt(media.get("addDate"))
                if not media_id or added is None:
                    continue
                delete_on = added.astimezone(tz).date() + timedelta(days=int(days))
                media_id = str(media_id).replace("-", "").lower()
                current = result.get(media_id)
                if current is None or delete_on < current.delete_on:
                    result[media_id] = PendingDeletion(media_id, delete_on, col.get("title", ""))
        return result


def parse_dt(value: str | None) -> datetime | None:
    """Parse ISO timestamps from Jellyfin/Maintainerr (7-digit fractions, 'Z' suffix)."""
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    if "." in v:
        head, _, rest = v.partition(".")
        frac = "".join(ch for ch in rest if ch.isdigit())
        tz = rest[len(frac):]
        v = f"{head}.{frac[:6]}{tz}"
    if "T" not in v and len(v) == 10:
        v += "T00:00:00+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
