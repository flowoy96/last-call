"""Weekly/daily schedule via APScheduler (single in-process job)."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db, digest

log = logging.getLogger("lastcall.scheduler")

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
FREQUENCIES = ["daily", "weekly", "biweekly", "monthly"]
JOB_ID = "digest"

scheduler = BackgroundScheduler(timezone=digest.local_tz())


def _job() -> None:
    log.info("scheduled run started")
    counts = digest.run()
    log.info("scheduled run finished: %s", counts)


def build_trigger(settings: dict) -> CronTrigger:
    """daily | weekly (on a weekday) | biweekly (that weekday in even ISO weeks)
    | monthly (first such weekday of the month)."""
    freq = settings.get("schedule_frequency", "weekly")
    day = settings.get("schedule_weekday", "fri")
    if day == "daily":  # value used by v1.0.0
        freq, day = "daily", "fri"
    if day not in WEEKDAYS:
        day = "fri"
    hour, _, minute = (settings.get("schedule_time") or "18:00").partition(":")
    common = dict(hour=int(hour or 18), minute=int(minute or 0), timezone=digest.local_tz())
    if freq == "daily":
        return CronTrigger(**common)
    if freq == "biweekly":
        return CronTrigger(week="*/2", day_of_week=day, **common)
    if freq == "monthly":
        return CronTrigger(day=f"1st {day}", **common)
    return CronTrigger(day_of_week=day, **common)


def apply(settings: dict | None = None) -> None:
    settings = settings or db.get_settings()
    if scheduler.get_job(JOB_ID):
        scheduler.remove_job(JOB_ID)
    if settings.get("schedule_enabled") != "1":
        return
    trigger = build_trigger(settings)
    scheduler.add_job(_job, trigger, id=JOB_ID, coalesce=True, max_instances=1, misfire_grace_time=3600)


def next_run():
    job = scheduler.get_job(JOB_ID)
    return job.next_run_time if job else None


def start() -> None:
    if not scheduler.running:
        scheduler.start()
    apply()


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
