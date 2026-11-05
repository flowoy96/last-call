"""Weekly/daily schedule via APScheduler (single in-process job)."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db, digest

log = logging.getLogger("lastcall.scheduler")

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
JOB_ID = "digest"

scheduler = BackgroundScheduler(timezone=digest.local_tz())


def _job() -> None:
    log.info("scheduled run started")
    counts = digest.run()
    log.info("scheduled run finished: %s", counts)


def apply(settings: dict | None = None) -> None:
    settings = settings or db.get_settings()
    if scheduler.get_job(JOB_ID):
        scheduler.remove_job(JOB_ID)
    if settings.get("schedule_enabled") != "1":
        return
    day = settings.get("schedule_weekday", "fri")
    hour, _, minute = (settings.get("schedule_time") or "18:00").partition(":")
    trigger = CronTrigger(
        day_of_week="*" if day == "daily" else day,
        hour=int(hour or 18),
        minute=int(minute or 0),
        timezone=digest.local_tz(),
    )
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
