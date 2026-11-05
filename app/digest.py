"""Build, render and send the per-user digest e-mail."""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

from . import db, mailer
from .clients import ApiError, Jellyfin, Maintainerr, PendingDeletion, parse_dt
from .i18n import format_date, t

log = logging.getLogger("lastcall.digest")

TEMPLATES = Path(__file__).parent / "templates"
POSTER_WIDTH = 220  # px; shown at ~170px, small enough to keep mails light
POSTER_CACHE_DAYS = 7
MAX_LEAVING = 30
MAX_NEW = 24

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
    sort_key: tuple = ()


@dataclass
class Digest:
    user_id: str
    user_name: str
    leaving: list[Card] = field(default_factory=list)
    new: list[Card] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.leaving and not self.new


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
# content
# --------------------------------------------------------------------------

def _year(item: dict) -> str:
    y = item.get("ProductionYear")
    return str(y) if y else ""


def _card_for_leaving(item: dict, lang: str) -> Card:
    s = t(lang)
    kind = item.get("Type")
    item_id = item["Id"]
    if kind == "Season":
        title = item.get("SeriesName") or item.get("Name", "")
        n = item.get("IndexNumber")
        sub = s["season"].format(n=n) if n is not None else item.get("Name", "")
        poster = item_id if item.get("ImageTags", {}).get("Primary") else item.get("SeriesId", item_id)
        return Card(item_id, poster, title, sub)
    if kind == "Episode":
        title = item.get("SeriesName") or item.get("Name", "")
        sub = f"S{item.get('ParentIndexNumber', 0):02d}E{item.get('IndexNumber', 0):02d} · {item.get('Name', '')}"
        return Card(item_id, item.get("SeriesId", item_id), title, sub)
    return Card(item_id, item_id, item.get("Name", ""), _year(item))


def build_digest(ctx: Context, user_id: str, user_name: str, since: datetime) -> Digest:
    lang = ctx.settings.get("language", "de")
    s = t(lang)
    today = datetime.now(ctx.tz).date()
    digest = Digest(user_id, user_name)

    # --- leaving soon (only items this user can access) ---
    if ctx.deletions:
        visible = ctx.jf.items_by_ids(user_id, list(ctx.deletions))
        for item in visible:
            pending = ctx.deletions.get(item["Id"].replace("-", "").lower())
            if not pending:
                continue
            card = _card_for_leaving(item, lang)
            when = max(pending.delete_on, today)
            card.badge = s["leaves_today"] if when == today else s["leaves_on"].format(date=format_date(when, lang))
            card.sort_key = (when, card.title.lower())
            digest.leaving.append(card)
        digest.leaving.sort(key=lambda c: c.sort_key)
        digest.leaving = digest.leaving[:MAX_LEAVING]

    # --- recently added ---
    leaving_ids = {c.item_id for c in digest.leaving}
    new_cards: list[tuple[datetime, Card]] = []
    seen_series: set[str] = set()
    for item in ctx.jf.recently_added(user_id, "Movie,Series", since):
        if item["Id"] in leaving_ids:
            continue
        created = parse_dt(item.get("DateCreated")) or since
        new_cards.append((created, Card(item["Id"], item["Id"], item.get("Name", ""), _year(item), s["new_badge"])))
        if item.get("Type") == "Series":
            seen_series.add(item["Id"])

    episodes: dict[str, list[dict]] = {}
    for ep in ctx.jf.recently_added(user_id, "Episode", since, limit=500):
        sid = ep.get("SeriesId")
        if sid and sid not in seen_series:
            episodes.setdefault(sid, []).append(ep)
    for sid, eps in episodes.items():
        newest = max((parse_dt(e.get("DateCreated")) or since) for e in eps)
        label = s["new_episode"] if len(eps) == 1 else s["new_episodes"].format(count=len(eps))
        new_cards.append((newest, Card(sid, sid, eps[0].get("SeriesName", ""), label, s["new_badge"])))

    new_cards.sort(key=lambda x: x[0], reverse=True)
    digest.new = [c for _, c in new_cards[:MAX_NEW]]
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
    return s["subject_new_only"].format(server=server, count=len(digest.new))


def render(digest: Digest, ctx: Context, image_src) -> tuple[str, str]:
    """Render (html, text). `image_src(card)` returns the <img> src or None."""
    settings = ctx.settings
    lang = settings.get("language", "de")
    server = settings.get("server_name") or "Jellyfin"
    base = (settings.get("jellyfin_public_url") or settings.get("jellyfin_url") or "").rstrip("/")
    data = dict(
        s=t(lang),
        lang=lang,
        server=server,
        name=digest.user_name,
        sections=[
            ("leaving", t(lang)["leaving_title"], t(lang)["leaving_text"], digest.leaving),
            ("new", t(lang)["new_title"], t(lang)["new_text"], digest.new),
        ],
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
    if digest.empty:
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
    db.log(
        recipient["name"], "sent",
        f"{recipient['email']}: {len(digest.leaving)} leaving, {len(digest.new)} new, "
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
