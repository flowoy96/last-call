"""Last Call web app (Starlette + Jinja2)."""

from __future__ import annotations

import logging
import os
import re
from html import escape
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from . import __version__, db, digest, mailer, scheduler, security
from .clients import ApiError, Jellyfin, Maintainerr
from .i18n import t

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lastcall")

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals["version"] = __version__


def _localtime(value: str | None) -> str:
    """Format a stored UTC ISO timestamp in the container's time zone."""
    from .clients import parse_dt
    dt = parse_dt(value) if value else None
    return dt.astimezone(digest.local_tz()).strftime("%Y-%m-%d %H:%M") if dt else "-"


templates.env.filters["localtime"] = _localtime

SETTING_FIELDS = [k for k in db.DEFAULTS]
URL_FIELDS = {"jellyfin_url", "jellyfin_public_url", "maintainerr_url"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def flash(request: Request, kind: str, message: str) -> None:
    request.session.setdefault("flash", []).append([kind, message])


def page(request: Request, name: str, status_code: int = 200, **ctx) -> Response:
    ctx.update(
        csrf=security.csrf_token(request.session),
        flashes=request.session.pop("flash", []),
        logged_in=bool(request.session.get("admin")),
        path=request.url.path,
    )
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def admin_exists() -> bool:
    return bool(db.get_setting("admin_password_hash"))


def login_required(handler):
    @wraps(handler)
    async def wrapper(request: Request):
        if not admin_exists():
            return redirect("/setup")
        if not request.session.get("admin"):
            return redirect("/login")
        if request.method == "POST":
            form = await request.form()
            if not security.csrf_valid(request.session, form.get("csrf")):
                return PlainTextResponse("Invalid CSRF token. Reload the page and try again.", status_code=403)
        return await handler(request)
    return wrapper


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# --------------------------------------------------------------------------
# setup / login
# --------------------------------------------------------------------------

async def setup(request: Request):
    if admin_exists():
        return redirect("/login")
    error = None
    if request.method == "POST":
        form = await request.form()
        if not security.csrf_valid(request.session, form.get("csrf")):
            return PlainTextResponse("Invalid CSRF token", status_code=403)
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""
        if not username:
            error = "Please choose a username."
        elif len(password) < 10:
            error = "The password must have at least 10 characters."
        elif password != form.get("password2"):
            error = "The passwords do not match."
        else:
            db.set_settings({"admin_user": username, "admin_password_hash": security.hash_password(password)})
            request.session.clear()
            request.session["admin"] = username
            flash(request, "ok", "Admin account created. Next: configure Jellyfin, Maintainerr and SMTP.")
            return redirect("/settings")
    return page(request, "setup.html", error=error)


async def login(request: Request):
    if not admin_exists():
        return redirect("/setup")
    error = None
    if request.method == "POST":
        form = await request.form()
        ip = client_ip(request)
        if not security.csrf_valid(request.session, form.get("csrf")):
            return PlainTextResponse("Invalid CSRF token", status_code=403)
        if security.login_limiter.blocked(ip):
            error = "Too many failed attempts. Please wait a few minutes."
            return page(request, "login.html", status_code=429, error=error)
        settings = db.get_settings()
        user_ok = (form.get("username") or "").strip() == settings.get("admin_user")
        pw_ok = security.verify_password(form.get("password") or "", settings.get("admin_password_hash", ""))
        if user_ok and pw_ok:
            security.login_limiter.reset(ip)
            request.session.clear()  # new session (and CSRF token) after login
            request.session["admin"] = settings["admin_user"]
            return redirect("/")
        security.login_limiter.hit(ip)
        log.warning("failed login from %s", ip)
        error = "Wrong username or password."
    return page(request, "login.html", error=error)


@login_required
async def logout(request: Request):
    request.session.clear()
    return redirect("/login")


# --------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------

@login_required
async def dashboard(request: Request):
    s = db.get_settings()
    recipients = db.list_recipients()
    status = {
        "jellyfin": bool(s["jellyfin_url"] and s["jellyfin_api_key"]),
        "maintainerr": bool(s["maintainerr_url"]),
        "smtp": bool(s["smtp_host"] and s["smtp_from_email"]),
        "recipients": sum(1 for r in recipients if r["enabled"] and r["email"]),
    }
    return page(request, "dashboard.html", status=status, next_run=scheduler.next_run(),
                log_rows=db.recent_log(), settings=s)


@login_required
async def run_now(request: Request):
    counts = await run_in_threadpool(digest.run)
    flash(request, "ok" if not counts.get("error") else "error",
          "Run finished: {sent} sent, {skipped} skipped, {error} errors.".format(
              sent=counts.get("sent", 0), skipped=counts.get("skipped", 0), error=counts.get("error", 0)))
    return redirect("/")


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

@login_required
async def settings_page(request: Request):
    current = db.get_settings()
    if request.method == "POST":
        form = await request.form()
        values: dict[str, str] = {}
        errors = []
        for key in SETTING_FIELDS:
            if key in db.CHECKBOX_KEYS:
                values[key] = "1" if form.get(key) else "0"
                continue
            raw = (form.get(key) or "").strip()
            if key in db.SECRET_KEYS and not raw:
                continue  # empty secret field = keep the stored value
            if key in URL_FIELDS and raw and not re.match(r"^https?://", raw):
                errors.append(f"{key.replace('_', ' ')} must start with http:// or https://")
                continue
            values[key] = raw
        if values.get("smtp_security") not in ("none", "starttls", "ssl"):
            values["smtp_security"] = "starttls"
        if values.get("language") not in ("de", "en"):
            values["language"] = "de"
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", values.get("schedule_time", "")):
            errors.append("Time must be in HH:MM format")
            values.pop("schedule_time", None)
        if values.get("schedule_weekday") not in scheduler.WEEKDAYS:
            values["schedule_weekday"] = "fri"
        if values.get("schedule_frequency") not in scheduler.FREQUENCIES:
            values["schedule_frequency"] = "weekly"
        for key in db.NUMERIC_KEYS:
            if not (values.get(key) or "").isdigit():
                errors.append(f"{key.replace('_', ' ')} must be a whole number")
                values.pop(key, None)
        for key in ("smtp_from_email",):
            if values.get(key) and not EMAIL_RE.match(values[key]):
                errors.append("Sender address is not a valid e-mail address")
                values.pop(key)
        for key in db.SECRET_KEYS:
            if form.get(f"clear_{key}"):
                values[key] = ""
        db.set_settings(values)
        scheduler.apply()
        for e in errors:
            flash(request, "error", e)
        if not errors:
            flash(request, "ok", "Settings saved.")
        return redirect("/settings")
    masked = {k: (bool(current[k]) if k in db.SECRET_KEYS else current[k]) for k in current}
    return page(request, "settings.html", s=masked, weekdays=scheduler.WEEKDAYS)


@login_required
async def test_mail(request: Request):
    form = await request.form()
    to = (form.get("to") or "").strip()
    if not EMAIL_RE.match(to):
        flash(request, "error", "Please enter a valid recipient address for the test e-mail.")
        return redirect("/settings#smtp")
    s = db.get_settings()
    strings = t(s.get("language", "de"))
    html = f"<p style='font-family:sans-serif'>{strings['test_body']}</p>"
    try:
        await run_in_threadpool(mailer.send, s, to, strings["test_subject"], html, strings["test_body"])
        flash(request, "ok", f"Test e-mail sent to {to}.")
        db.log("test", "sent", to)
    except Exception as e:  # noqa: BLE001
        flash(request, "error", f"Test e-mail failed: {e}")
        db.log("test", "error", str(e))
    return redirect("/settings#smtp")


def _test_service(service: str) -> str:
    s = db.get_settings()
    if service == "jellyfin":
        with Jellyfin(s["jellyfin_url"], s["jellyfin_api_key"]) as jf:
            info = jf.system_info()
            users = jf.users()
        return f"Jellyfin OK: {info.get('ServerName', '?')} {info.get('Version', '')}, {len(users)} users."
    with Maintainerr(s["maintainerr_url"], s["maintainerr_api_key"]) as m:
        pending = m.pending_deletions(digest.local_tz())
    return f"Maintainerr OK: {len(pending)} items scheduled for removal."


@login_required
async def test_service(request: Request):
    service = request.path_params["service"]
    if service not in ("jellyfin", "maintainerr"):
        return PlainTextResponse("not found", status_code=404)
    try:
        flash(request, "ok", await run_in_threadpool(_test_service, service))
    except Exception as e:  # noqa: BLE001
        flash(request, "error", str(e))
    return redirect(f"/settings#{service}")


# --------------------------------------------------------------------------
# recipients
# --------------------------------------------------------------------------

@login_required
async def recipients(request: Request):
    if request.method == "POST":
        form = await request.form()
        errors = 0
        for r in db.list_recipients():
            rid = r["jellyfin_id"]
            email = (form.get(f"email_{rid}") or "").strip()
            if email and not EMAIL_RE.match(email):
                flash(request, "error", f"Invalid e-mail address for {r['name']}.")
                errors += 1
                continue
            db.update_recipient(rid, email, bool(form.get(f"enabled_{rid}")) and bool(email))
        if not errors:
            flash(request, "ok", "Recipients saved.")
        return redirect("/recipients")
    return page(request, "recipients.html", recipients=db.list_recipients())


def _import_users() -> tuple[int, int]:
    s = db.get_settings()
    with Jellyfin(s["jellyfin_url"], s["jellyfin_api_key"]) as jf:
        users = jf.users()
    existing = {r["jellyfin_id"] for r in db.list_recipients()}
    jf_ids = set()
    for u in users:
        jf_ids.add(u["Id"])
        db.upsert_recipient_name(u["Id"], u["Name"])
    removed = 0
    for rid in existing - jf_ids:
        db.delete_recipient(rid)
        removed += 1
    return len(jf_ids - existing), removed


@login_required
async def import_users(request: Request):
    try:
        added, removed = await run_in_threadpool(_import_users)
        flash(request, "ok", f"Imported from Jellyfin: {added} new, {removed} removed.")
    except ApiError as e:
        flash(request, "error", str(e))
    return redirect("/recipients")


# --------------------------------------------------------------------------
# preview
# --------------------------------------------------------------------------

def _preview_html(user_id: str) -> tuple[str, str]:
    r = db.get_recipient(user_id)
    if r is None:
        raise ApiError("Unknown recipient")
    ctx = digest.open_context(db.get_settings())
    try:
        d = digest.build_digest(ctx, r["jellyfin_id"], r["name"], digest._since(r, ctx.settings))
        if d.empty:
            return "", "(empty: this mail would not be sent)"
        html, _ = digest.render(d, ctx, lambda c: f"/poster/{c.poster_id}")
        if not d.should_send(ctx.settings):
            note = ("<div style='font-family:sans-serif;background:#3f3f46;color:#fff;padding:12px 16px'>"
                    "Preview only: nothing is leaving and nothing is new, so this mail would <b>not</b> be sent. "
                    "Enable \"Send when there are only suggestions\" in the settings to change that.</div>")
            html = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + note, html, count=1)
        return html, digest.subject(d, ctx.settings)
    finally:
        ctx.jf.close()


@login_required
async def preview(request: Request):
    users = db.list_recipients()
    selected = request.query_params.get("user") or (users[0]["jellyfin_id"] if users else "")
    return page(request, "preview.html", users=users, selected=selected)


@login_required
async def preview_frame(request: Request):
    user_id = request.query_params.get("user", "")
    try:
        html, subj = await run_in_threadpool(_preview_html, user_id)
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(f"<p style='font-family:sans-serif;color:#c00;padding:2rem'>Preview failed: {escape(str(e))}</p>")
    if not html:
        return HTMLResponse("<p style='font-family:sans-serif;color:#888;padding:2rem'>"
                            "Nothing leaving and nothing new for this user. No e-mail would be sent.</p>")
    return HTMLResponse(html)


@login_required
async def preview_send(request: Request):
    form = await request.form()
    user_id = form.get("user") or ""
    r = db.get_recipient(user_id)
    if not r or not r["email"]:
        flash(request, "error", "This user has no e-mail address.")
        return redirect(f"/preview?user={user_id}")
    if not r["enabled"]:
        flash(request, "error", "This recipient is disabled. Enable them on the Recipients page first.")
        return redirect(f"/preview?user={user_id}")
    counts = await run_in_threadpool(digest.run, user_id)
    summary = ", ".join(f"{k}: {v}" for k, v in counts.items() if v) or "nothing to do"
    flash(request, "ok" if counts.get("sent") else "error", f"{r['name']}: {summary} (see log on the dashboard)")
    return redirect(f"/preview?user={user_id}")


@login_required
async def poster(request: Request):
    item_id = request.path_params["item_id"]
    s = db.get_settings()

    def load():
        with Jellyfin(s["jellyfin_url"], s["jellyfin_api_key"]) as jf:
            return digest.poster(jf, item_id)

    try:
        data = await run_in_threadpool(load)
    except ApiError:
        data = None
    if not data:
        return Response(status_code=404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


async def health(request: Request):
    try:
        db.get_setting("language")
    except Exception:  # noqa: BLE001
        return PlainTextResponse("db error", status_code=503)
    return PlainTextResponse("ok")


# --------------------------------------------------------------------------
# app
# --------------------------------------------------------------------------

class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"SAMEORIGIN"),
                    (b"referrer-policy", b"same-origin"),
                    (b"content-security-policy",
                     b"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                     b"script-src 'self'; frame-ancestors 'self'; form-action 'self'"),
                ]
            await send(message)

        await self.app(scope, receive, send_wrapper)


@asynccontextmanager
async def lifespan(app):
    scheduler.start()
    log.info("Last Call %s started", __version__)
    yield
    scheduler.stop()


def create_app() -> Starlette:
    db.init()
    routes = [
        Route("/", dashboard),
        Route("/setup", setup, methods=["GET", "POST"]),
        Route("/login", login, methods=["GET", "POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/run", run_now, methods=["POST"]),
        Route("/settings", settings_page, methods=["GET", "POST"]),
        Route("/settings/test-mail", test_mail, methods=["POST"]),
        Route("/settings/test/{service}", test_service, methods=["POST"]),
        Route("/recipients", recipients, methods=["GET", "POST"]),
        Route("/recipients/import", import_users, methods=["POST"]),
        Route("/preview", preview),
        Route("/preview/frame", preview_frame),
        Route("/preview/send", preview_send, methods=["POST"]),
        Route("/poster/{item_id}", poster),
        Route("/health", health),
        Mount("/static", StaticFiles(directory=BASE / "static"), name="static"),
    ]
    middleware = [
        Middleware(SecurityHeaders),
        Middleware(
            SessionMiddleware,
            secret_key=security.session_secret(),
            session_cookie="lastcall_session",
            max_age=7 * 24 * 3600,
            same_site="lax",
            https_only=os.environ.get("LASTCALL_SECURE_COOKIES", "0") == "1",
        ),
    ]
    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)


app = create_app()
