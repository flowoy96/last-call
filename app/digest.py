"""Build, render and send the per-user digest e-mail."""

from __future__ import annotations

import io
import logging
import math
import os
import random
import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

from . import db, mailer
from .clients import ApiError, Jellyfin, Maintainerr, PendingDeletion, parse_dt
from .i18n import format_date, format_month, t

log = logging.getLogger("lastcall.digest")

TEMPLATES = Path(__file__).parent / "templates"
POSTER_WIDTH = 220  # px; shown at ~170px, small enough to keep mails light
POSTER_CACHE_DAYS = 7
MAX_LEAVING = 30
MAX_NEW = 24
MAX_SUGGESTIONS = 12

SECTION_COLORS = {
    # key: (accent bar, badge background)
    "leaving": ("#e11d48", "#e11d48"),
    "new": ("#22c55e", "#15803d"),
    "reco": ("#8b5cf6", "#6d28d9"),
    "rewatch": ("#f59e0b", "#b45309"),
}

_env = Environment(loader=FileSystemLoader(TEMPLATES / "mail"), autoescape=select_autoescape(["html"]))
_run_lock = threading.Lock()


def local_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.environ.get("TZ", "Europe/Berlin"))
    except Exception:
        return ZoneInfo("UTC")


@dataclass
class Card:
    item_id: str        # item opened by the deep link
    poster_id: str      # item whose primary image is used
    title: str
    subtitle: str = ""
    badge: str = ""
    keys: frozenset = frozenset()
    sort_key: tuple = ()


@dataclass
class Digest:
    user_id: str
    user_name: str
    leaving: list[Card] = field(default_factory=list)
    new: list[Card] = field(default_factory=list)
    reco: list[Card] = field(default_factory=list)
    rewatch: list[Card] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.leaving or self.new or self.reco or self.rewatch)

    def should_send(self, settings: dict) -> bool:
        """Leaving/new always justify a mail; suggestions alone only if enabled."""
        if self.leaving or self.new:
            return True
        return settings.get("send_suggestions_only") == "1" and bool(self.reco or self.rewatch)


@dataclass
class Context:
    """Data shared across all recipients of one run."""
    settings: dict
    jf: Jellyfin
    server_id: str
    deletions: dict[str, PendingDeletion]
    tz: ZoneInfo


def open_context(settings: dict) -> Context:
    jf = Jellyfin(settings["jellyfin_url"], settings["jellyfin_api_key"])
    try:
        server_id = jf.system_info().get("Id", "")
        deletions: dict[str, PendingDeletion] = {}
        if settings.get("maintainerr_url"):
            with Maintainerr(settings["maintainerr_url"], settings.get("maintainerr_api_key", "")) as m:
                deletions = m.pending_deletions(local_tz())
    except Exception:
        jf.close()
        raise
    return Context(settings, jf, server_id, deletions, local_tz())


# --------------------------------------------------------------------------
# duplicate detection
# --------------------------------------------------------------------------
#
# The same title can exist several times in Jellyfin, e.g. a 1080p copy in
# "Movies" and a 4K copy in "Movies 4K" (versions in one folder are already
# merged by Jellyfin). Such copies have different item ids, so items are
# compared by "keys": provider ids (TMDb/IMDb/TVDB) plus normalised name+year.
# Two items sharing any key are treated as the same title.

DEDUP_PROVIDERS = {"tmdb", "imdb", "tvdb"}


def _norm(text: str | None) -> str:
    return re.sub(r"[^0-9a-z]+", "", (text or "").lower())


def norm_id(item_id: str) -> str:
    return item_id.replace("-", "").lower()


def item_keys(item: dict) -> frozenset:
    kind = item.get("Type", "")
    keys = {f"id:{norm_id(item['Id'])}"}
    if kind == "Season":
        keys.add(f"season:{_norm(item.get('SeriesName'))}:{item.get('IndexNumber')}")
        return frozenset(keys)
    if kind == "Episode":
        keys.add(f"episode:{_norm(item.get('SeriesName'))}:{item.get('ParentIndexNumber')}:{item.get('IndexNumber')}")
        return frozenset(keys)
    for provider, value in (item.get("ProviderIds") or {}).items():
        if value and provider.lower() in DEDUP_PROVIDERS:
            keys.add(f"{kind}:{provider.lower()}:{str(value).lower()}")
    name = _norm(item.get("Name"))
    if name:
        keys.add(f"{kind}:name:{name}:{item.get('ProductionYear') or ''}")
    return frozenset(keys)


class Library:
    """A user's movies and series, indexed for duplicate lookups."""

    def __init__(self, items: list[dict]):
        self.items = items
        self.by_id = {norm_id(i["Id"]): i for i in items}
        self._index: dict[str, list[dict]] = defaultdict(list)
        for it in items:
            it["_keys"] = item_keys(it)
            for k in it["_keys"]:
                self._index[k].append(it)

    def copies(self, item: dict) -> list[dict]:
        """Other library items that are the same title as `item`."""
        own = norm_id(item["Id"])
        found = {}
        for k in item_keys(item):
            for other in self._index.get(k, ()):
                if norm_id(other["Id"]) != own:
                    found[norm_id(other["Id"])] = other
        return list(found.values())


class Seen:
    """Keys of everything already placed in the mail (across all sections)."""

    def __init__(self):
        self.keys: set[str] = set()

    def has(self, keys) -> bool:
        return not self.keys.isdisjoint(keys)

    def add(self, keys) -> None:
        self.keys |= set(keys)


# --------------------------------------------------------------------------
# content
# --------------------------------------------------------------------------

def _year(item: dict) -> str:
    y = item.get("ProductionYear")
    return str(y) if y else ""


def _int(settings: dict, key: str, default: int) -> int:
    try:
        return max(0, int(settings.get(key) or default))
    except ValueError:
        return default


def _card_for_leaving(item: dict, lang: str) -> Card:
    s = t(lang)
    kind = item.get("Type")
    item_id = item["Id"]
    keys = item_keys(item)
    if kind == "Season":
        title = item.get("SeriesName") or item.get("Name", "")
        n = item.get("IndexNumber")
        sub = s["season"].format(n=n) if n is not None else item.get("Name", "")
        poster = item_id if item.get("ImageTags", {}).get("Primary") else item.get("SeriesId", item_id)
        return Card(item_id, poster, title, sub, keys=keys)
    if kind == "Episode":
        title = item.get("SeriesName") or item.get("Name", "")
        sub = f"S{item.get('ParentIndexNumber') or 0:02d}E{item.get('IndexNumber') or 0:02d} · {item.get('Name', '')}"
        return Card(item_id, item.get("SeriesId", item_id), title, sub, keys=keys)
    return Card(item_id, item_id, item.get("Name", ""), _year(item), keys=keys)


def _user_data(item: dict) -> dict:
    return item.get("UserData") or {}


def _watched(item: dict) -> bool:
    ud = _user_data(item)
    return bool(ud.get("Played") or ud.get("PlayCount") or (ud.get("PlayedPercentage") or 0) > 0
                or ud.get("PlaybackPositionTicks"))


def _weighted_pick(rnd: random.Random, candidates: list[tuple[float, dict]], k: int) -> list[dict]:
    """Weighted random sample without replacement (Efraimidis–Spirakis)."""
    keyed = [(rnd.random() ** (1.0 / max(w, 1e-6)), it) for w, it in candidates]
    keyed.sort(key=lambda x: x[0], reverse=True)
    return [it for _, it in keyed[:k]]


def _leaving(ctx: Context, user_id: str, lib: Library, seen: Seen, lang: str) -> list[Card]:
    if not ctx.deletions:
        return []
    s = t(lang)
    today = datetime.now(ctx.tz).date()
    visible = ctx.jf.items_by_ids(user_id, list(ctx.deletions))
    visible.sort(key=lambda i: ctx.deletions.get(norm_id(i["Id"]), PendingDeletion("", today, "")).delete_on)
    cards = []
    for item in visible:
        pending = ctx.deletions.get(norm_id(item["Id"]))
        if not pending:
            continue
        keys = item_keys(item)
        if seen.has(keys):
            continue  # another copy of this title is already listed
        if item.get("Type") in ("Movie", "Series"):
            remaining = [c for c in lib.copies(item) if norm_id(c["Id"]) not in ctx.deletions]
            if remaining:
                seen.add(keys)
                continue  # e.g. the 1080p copy goes, the 4K copy stays: nothing is really leaving
        card = _card_for_leaving(item, lang)
        when = max(pending.delete_on, today)
        card.badge = s["leaves_today"] if when == today else s["leaves_on"].format(date=format_date(when, lang))
        card.sort_key = (when, card.title.lower())
        seen.add(keys)
        cards.append(card)
    cards.sort(key=lambda c: c.sort_key)
    return cards[:MAX_LEAVING]


def _new(ctx: Context, user_id: str, lib: Library, seen: Seen, since: datetime, lang: str) -> list[Card]:
    s = t(lang)
    found: list[tuple[datetime, Card]] = []
    for item in ctx.jf.recently_added(user_id, "Movie,Series", since):
        keys = item_keys(item)
        if seen.has(keys):
            continue
        seen.add(keys)
        older = [c for c in lib.copies(item) if (parse_dt(c.get("DateCreated")) or since) < since]
        if older:
            continue  # just another version (e.g. 4K) of a title that was already there
        created = parse_dt(item.get("DateCreated")) or since
        found.append((created, Card(item["Id"], item["Id"], item.get("Name", ""), _year(item),
                                    s["new_badge"], keys=keys)))

    # New episodes, grouped per series. Copies of a series in several libraries
    # are merged, and the same episode in several qualities is counted once.
    groups: list[dict] = []
    for ep in ctx.jf.recently_added(user_id, "Episode", since, limit=500):
        sid = ep.get("SeriesId")
        if not sid:
            continue
        series = lib.by_id.get(norm_id(sid))
        skeys = series["_keys"] if series else frozenset({f"id:{norm_id(sid)}"})
        if seen.has(skeys):
            continue  # whole series is already shown as new (or leaving)
        group = next((g for g in groups if not g["keys"].isdisjoint(skeys)), None)
        if group is None:
            group = {"keys": set(skeys), "sid": sid, "name": ep.get("SeriesName", ""), "eps": set(), "newest": since}
            groups.append(group)
        group["keys"] |= skeys
        group["eps"].add((ep.get("ParentIndexNumber"), ep.get("IndexNumber"), _norm(ep.get("Name"))))
        group["newest"] = max(group["newest"], parse_dt(ep.get("DateCreated")) or since)
    for g in groups:
        n = len(g["eps"])
        label = s["new_episode"] if n == 1 else s["new_episodes"].format(count=n)
        seen.add(g["keys"])
        found.append((g["newest"], Card(g["sid"], g["sid"], g["name"], label, s["new_badge"], keys=frozenset(g["keys"]))))

    found.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in found[:MAX_NEW]]


def _recommendations(ctx: Context, lib: Library, seen: Seen, history: set[str], rnd: random.Random,
                     lang: str) -> list[Card]:
    """Unwatched movies and series that match the genres the user watches most."""
    count = min(_int(ctx.settings, "reco_count", 6), MAX_SUGGESTIONS)
    if ctx.settings.get("reco_enabled") != "1" or count == 0:
        return []
    s = t(lang)
    now = datetime.now(timezone.utc)

    affinity: Counter = Counter()
    for it in lib.items:
        ud = _user_data(it)
        if not (_watched(it) or ud.get("IsFavorite")):
            continue
        weight = 1.0 + (2.0 if ud.get("IsFavorite") else 0.0)
        last = parse_dt(ud.get("LastPlayedDate"))
        if last and (now - last).days < 180:
            weight += 1.0  # recent taste counts a bit more
        for g in it.get("Genres") or []:
            affinity[g] += weight
    total = sum(affinity.values()) or 1.0

    pools: dict[str, list[tuple[float, dict]]] = {"Movie": [], "Series": []}
    for it in lib.items:
        if it.get("Type") not in pools or _watched(it):
            continue
        keys = it["_keys"]
        if seen.has(keys) or not history.isdisjoint(keys):
            continue
        if any(_watched(c) for c in lib.copies(it)):
            continue  # watched in another version
        genres = it.get("Genres") or []
        match = sum(affinity[g] for g in genres) / total / math.sqrt(max(len(genres), 1))
        rating = float(it.get("CommunityRating") or 6.0) / 10.0
        pools[it["Type"]].append((0.05 + 3.0 * match + 0.5 * rating, it))

    picked: list[dict] = []
    movies_wanted = math.ceil(count / 2)
    for kind, want in (("Movie", movies_wanted), ("Series", count - movies_wanted)):
        pool = sorted(pools[kind], key=lambda x: x[0], reverse=True)[: max(want * 4, 12)]
        picked += _pick_unique(rnd, pool, want, seen)
    if len(picked) < count:  # fill up from whatever type still has candidates
        rest = sorted(pools["Movie"] + pools["Series"], key=lambda x: x[0], reverse=True)
        picked += _pick_unique(rnd, [p for p in rest if p[1] not in picked][: count * 4], count - len(picked), seen)

    cards = []
    for it in picked:
        genres = sorted(it.get("Genres") or [], key=lambda g: affinity[g], reverse=True)
        sub = " · ".join(x for x in (_year(it), genres[0] if genres else "") if x)
        cards.append(Card(it["Id"], it["Id"], it.get("Name", ""), sub, s["reco_badge"], keys=it["_keys"]))
    return cards


def _rewatch(ctx: Context, lib: Library, seen: Seen, history: set[str], rnd: random.Random,
             lang: str) -> list[Card]:
    """A handful of movies the user has seen, but not for a long time."""
    count = min(_int(ctx.settings, "rewatch_count", 3), MAX_SUGGESTIONS)
    if ctx.settings.get("rewatch_enabled") != "1" or count == 0:
        return []
    s = t(lang)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=_int(ctx.settings, "rewatch_min_days", 180))

    # Watch state per title, merged over all copies of a movie.
    candidates: list[tuple[float, dict]] = []
    handled: set[str] = set()
    for it in lib.items:
        if it.get("Type") != "Movie" or norm_id(it["Id"]) in handled:
            continue
        group = [it] + lib.copies(it)
        handled |= {norm_id(c["Id"]) for c in group}
        played = [c for c in group if _user_data(c).get("Played") or _user_data(c).get("PlayCount")]
        if not played:
            continue
        keys = frozenset().union(*(c["_keys"] for c in group))
        if seen.has(keys) or not history.isdisjoint(keys):
            continue
        dates = [d for d in (parse_dt(_user_data(c).get("LastPlayedDate")) for c in group) if d]
        last = max(dates) if dates else None
        if last and last > cutoff:
            continue  # watched recently
        fav = any(_user_data(c).get("IsFavorite") for c in group)
        years_ago = ((now - last).days / 365) if last else 1.0
        rating = float(it.get("CommunityRating") or 6.0) / 10.0
        target = played[0]
        target["_last"], target["_keys_all"] = last, keys
        candidates.append((1.0 + (2.0 if fav else 0.0) + rating + min(years_ago, 3.0) * 0.3, target))

    cards = []
    for it in _pick_unique(rnd, candidates, count, seen):
        last = it.get("_last")
        badge = s["rewatch_last"].format(date=format_month(last.astimezone(ctx.tz), lang)) if last else s["rewatch_badge"]
        cards.append(Card(it["Id"], it["Id"], it.get("Name", ""), _year(it), badge, keys=it["_keys_all"]))
    return cards


def _pick_unique(rnd: random.Random, pool: list[tuple[float, dict]], want: int, seen: Seen) -> list[dict]:
    out = []
    for it in _weighted_pick(rnd, pool, len(pool)):
        if len(out) >= want:
            break
        keys = it.get("_keys_all") or it["_keys"]
        if seen.has(keys):
            continue
        seen.add(keys)
        out.append(it)
    return out


def build_digest(ctx: Context, user_id: str, user_name: str, since: datetime) -> Digest:
    lang = ctx.settings.get("language", "de")
    lib = Library(ctx.jf.library(user_id))
    seen = Seen()
    digest = Digest(user_id, user_name)
    digest.leaving = _leaving(ctx, user_id, lib, seen, lang)
    digest.new = _new(ctx, user_id, lib, seen, since, lang)

    history = db.recent_suggestions(user_id, _int(ctx.settings, "suggestion_cooldown_days", 90))
    # Same picks for the same user on the same day, so preview and sent mail match.
    rnd = random.Random(f"{user_id}:{datetime.now(ctx.tz).date().isoformat()}")
    digest.reco = _recommendations(ctx, lib, seen, history, rnd, lang)
    digest.rewatch = _rewatch(ctx, lib, seen, history, rnd, lang)
    return digest


# --------------------------------------------------------------------------
# posters
# --------------------------------------------------------------------------

def _cache_dir() -> Path:
    p = db.DATA_DIR / "cache" / "posters"
    p.mkdir(parents=True, exist_ok=True)
    return p


def poster(jf: Jellyfin, item_id: str) -> bytes | None:
    """Return a small JPEG poster for `item_id`, cached on disk."""
    safe = "".join(ch for ch in item_id if ch.isalnum())
    if not safe:
        return None
    path = _cache_dir() / f"{safe}.jpg"
    if path.exists() and time.time() - path.stat().st_mtime < POSTER_CACHE_DAYS * 86400:
        return path.read_bytes()
    raw = jf.image(safe, max_width=POSTER_WIDTH * 2)
    if not raw:
        return None
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        if img.width > POSTER_WIDTH:
            img = img.resize((POSTER_WIDTH, round(img.height * POSTER_WIDTH / img.width)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=72, optimize=True, progressive=True)
    except Exception:
        log.warning("could not process poster %s", safe)
        return None
    data = buf.getvalue()
    path.write_bytes(data)
    return data


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def deep_link(settings: dict, server_id: str, item_id: str) -> str:
    base = (settings.get("jellyfin_public_url") or settings.get("jellyfin_url") or "").rstrip("/")
    link = f"{base}/web/#/details?id={item_id}"
    return f"{link}&serverId={server_id}" if server_id else link


def subject(digest: Digest, settings: dict) -> str:
    s = t(settings.get("language", "de"))
    server = settings.get("server_name") or "Jellyfin"
    if digest.leaving:
        return s["subject"].format(server=server, leaving=len(digest.leaving))
    if digest.new:
        return s["subject_new_only"].format(server=server, count=len(digest.new))
    return s["subject_suggestions"].format(server=server)


def render(digest: Digest, ctx: Context, image_src) -> tuple[str, str]:
    """Render (html, text). `image_src(card)` returns the <img> src or None."""
    settings = ctx.settings
    lang = settings.get("language", "de")
    s = t(lang)
    server = settings.get("server_name") or "Jellyfin"
    base = (settings.get("jellyfin_public_url") or settings.get("jellyfin_url") or "").rstrip("/")
    data = dict(
        s=s,
        lang=lang,
        server=server,
        name=digest.user_name,
        sections=[
            ("leaving", s["leaving_title"], s["leaving_text"], digest.leaving),
            ("new", s["new_title"], s["new_text"], digest.new),
            ("reco", s["reco_title"], s["reco_text"], digest.reco),
            ("rewatch", s["rewatch_title"], s["rewatch_text"], digest.rewatch),
        ],
        colors=SECTION_COLORS,
        link=lambda c: deep_link(settings, ctx.server_id, c.item_id),
        img=image_src,
        server_url=f"{base}/web/" if base else "",
        subject=subject(digest, settings),
    )
    return _env.get_template("digest.html").render(**data), _env.get_template("digest.txt").render(**data)


def cid_for(card: Card) -> str:
    return f"poster-{card.poster_id}@lastcall"


# --------------------------------------------------------------------------
# sending
# --------------------------------------------------------------------------

def _since(recipient, settings: dict) -> datetime:
    last = parse_dt(recipient["last_sent"]) if recipient["last_sent"] else None
    if last:
        return last
    days = int(settings.get("new_lookback_days") or 7)
    return datetime.now(timezone.utc) - timedelta(days=days)


def send_to(ctx: Context, recipient) -> str:
    """Build and send one digest. Returns a status string for the log."""
    started = db.now_iso()
    digest = build_digest(ctx, recipient["jellyfin_id"], recipient["name"], _since(recipient, ctx.settings))
    if not digest.should_send(ctx.settings):
        db.log(recipient["name"], "skipped", "nothing leaving and nothing new")
        return "skipped"

    images: dict[str, bytes] = {}

    def src(card: Card):
        if card.poster_id not in images:
            data = poster(ctx.jf, card.poster_id)
            if data is None:
                return None
            images[card.poster_id] = data
        return f"cid:{cid_for(card)}"

    html, text = render(digest, ctx, src)
    attachments = {f"poster-{pid}@lastcall": data for pid, data in images.items()}
    mailer.send(ctx.settings, recipient["email"], subject(digest, ctx.settings), html, text, attachments)
    db.mark_sent(recipient["jellyfin_id"], started)
    # Remember what this user was shown, so later mails don't suggest it again.
    suggested = {k for c in digest.new + digest.reco + digest.rewatch for k in c.keys}
    if suggested:
        db.record_suggestions(recipient["jellyfin_id"], suggested)
    db.log(
        recipient["name"], "sent",
        f"{recipient['email']}: {len(digest.leaving)} leaving, {len(digest.new)} new, "
        f"{len(digest.reco)} recommended, {len(digest.rewatch)} rewatch, "
        f"{sum(len(v) for v in images.values()) // 1024} KiB images",
    )
    return "sent"


def run(only_user: str | None = None) -> dict[str, int]:
    """Send digests to all enabled recipients (or one). Safe to call from any thread."""
    if not _run_lock.acquire(blocking=False):
        db.log("-", "error", "a run is already in progress")
        return {"error": 1}
    counts = {"sent": 0, "skipped": 0, "error": 0}
    try:
        settings = db.get_settings()
        recipients = [
            r for r in db.list_recipients()
            if r["enabled"] and r["email"] and (only_user is None or r["jellyfin_id"] == only_user)
        ]
        if not recipients:
            db.log("-", "skipped", "no enabled recipients with an e-mail address")
            return counts
        try:
            ctx = open_context(settings)
        except (ApiError, Exception) as e:  # noqa: BLE001 - surface any setup problem in the log
            db.log("-", "error", str(e))
            counts["error"] += 1
            return counts
        try:
            for r in recipients:
                try:
                    counts[send_to(ctx, r)] += 1
                except Exception as e:  # noqa: BLE001
                    log.exception("sending to %s failed", r["name"])
                    db.log(r["name"], "error", str(e))
                    counts["error"] += 1
        finally:
            ctx.jf.close()
    finally:
        _run_lock.release()
    return counts
