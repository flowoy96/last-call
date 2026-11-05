"""SMTP delivery with inline (CID) images."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid


def _connect(settings: dict) -> smtplib.SMTP:
    host = settings.get("smtp_host", "").strip()
    if not host:
        raise RuntimeError("SMTP host is not configured")
    port = int(settings.get("smtp_port") or 587)
    security = settings.get("smtp_security", "starttls")
    ctx = ssl.create_default_context()
    if security == "ssl":
        conn: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=30, context=ctx)
    else:
        conn = smtplib.SMTP(host, port, timeout=30)
        conn.ehlo()
        if security == "starttls":
            conn.starttls(context=ctx)
            conn.ehlo()
    if settings.get("smtp_user"):
        conn.login(settings["smtp_user"], settings.get("smtp_password", ""))
    return conn


def build_message(settings: dict, to: str, subject: str, html: str, text: str,
                  images: dict[str, bytes] | None = None) -> EmailMessage:
    sender = settings.get("smtp_from_email", "").strip()
    if not sender:
        raise RuntimeError("Sender address is not configured")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.get("smtp_from_name") or "Last Call", sender))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    html_part = msg.get_payload()[1]
    for cid, data in (images or {}).items():
        html_part.add_related(data, maintype="image", subtype="jpeg", cid=f"<{cid}>",
                              filename=cid.split("@")[0] + ".jpg", disposition="inline")
    return msg


def send(settings: dict, to: str, subject: str, html: str, text: str,
         images: dict[str, bytes] | None = None) -> None:
    msg = build_message(settings, to, subject, html, text, images)
    with _connect(settings) as conn:
        conn.send_message(msg)
