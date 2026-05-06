"""APScheduler wrapper that fires webhooks on schedule."""
import json
import time
import logging
import datetime as dt
from typing import Optional

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from models import SessionLocal, Schedule, Execution, new_id

log = logging.getLogger("scheduler")
scheduler = BackgroundScheduler(timezone="UTC")

MAX_RESPONSE_BODY = 2048  # truncate webhook responses


def fire_webhook(schedule_id: str) -> None:
    """Execute the webhook for a schedule and log the result."""
    db = SessionLocal()
    try:
        sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if not sched or not sched.is_active:
            return

        headers = json.loads(sched.webhook_headers) if isinstance(sched.webhook_headers, str) else sched.webhook_headers
        body = json.loads(sched.webhook_body) if isinstance(sched.webhook_body, str) else sched.webhook_body

        execution = Execution(
            id=new_id(),
            schedule_id=schedule_id,
            triggered_at=dt.datetime.utcnow(),
            status="pending",
        )
        db.add(execution)
        db.commit()

        start = time.monotonic()
        try:
            with httpx.Client(timeout=30) as client:
                if body:
                    resp = client.post(sched.webhook_url, headers=headers, json=body)
                else:
                    resp = client.post(sched.webhook_url, headers=headers)

            elapsed_ms = int((time.monotonic() - start) * 1000)
            execution.status = "success" if resp.status_code < 400 else "failed"
            execution.response_code = resp.status_code
            execution.response_body = resp.text[:MAX_RESPONSE_BODY]
            execution.duration_ms = elapsed_ms

            # Retry once on 5xx
            if 500 <= resp.status_code < 600:
                log.warning(f"Schedule {schedule_id}: got {resp.status_code}, retrying in 30s")
                time.sleep(30)
                if body:
                    resp2 = httpx.post(sched.webhook_url, headers=headers, json=body, timeout=30)
                else:
                    resp2 = httpx.post(sched.webhook_url, headers=headers, timeout=30)
                if resp2.status_code < 400:
                    execution.status = "success"
                    execution.response_code = resp2.status_code
                    execution.response_body = resp2.text[:MAX_RESPONSE_BODY]

        except httpx.TimeoutException:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            execution.status = "timeout"
            execution.duration_ms = elapsed_ms
            execution.response_body = "Request timed out after 30s"
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            execution.status = "failed"
            execution.duration_ms = elapsed_ms
            execution.response_body = str(exc)[:MAX_RESPONSE_BODY]

        sched.last_run_at = execution.triggered_at
        # Update next_run_at from APScheduler
        job = scheduler.get_job(schedule_id)
        if job and job.next_run_time:
            sched.next_run_at = job.next_run_time.replace(tzinfo=None)
        db.commit()
        log.info(f"Schedule {schedule_id} fired: {execution.status} ({execution.duration_ms}ms)")
    except Exception:
        log.exception(f"Error firing webhook for schedule {schedule_id}")
        db.rollback()
    finally:
        db.close()


def _make_trigger(sched: Schedule):
    """Build an APScheduler trigger from a Schedule model."""
    if sched.cron_expr:
        parts = sched.cron_expr.strip().split()
        if len(parts) == 5:
            return CronTrigger(
                minute=parts[0], hour=parts[1], day=parts[2],
                month=parts[3], day_of_week=parts[4],
                timezone=sched.timezone or "UTC",
            )
        elif len(parts) == 6:
            return CronTrigger(
                second=parts[0], minute=parts[1], hour=parts[2],
                day=parts[3], month=parts[4], day_of_week=parts[5],
                timezone=sched.timezone or "UTC",
            )
    if sched.interval_seconds:
        return IntervalTrigger(seconds=sched.interval_seconds, timezone=sched.timezone or "UTC")
    return None


def register_schedule(sched: Schedule) -> Optional[dt.datetime]:
    """Add or replace a schedule in APScheduler. Returns next_run_at."""
    trigger = _make_trigger(sched)
    if not trigger:
        return None

    scheduler.add_job(
        fire_webhook,
        trigger=trigger,
        args=[sched.id],
        id=sched.id,
        replace_existing=True,
        misfire_grace_time=60,
    )
    job = scheduler.get_job(sched.id)
    if job and job.next_run_time:
        return job.next_run_time.replace(tzinfo=None)
    return None


def unregister_schedule(schedule_id: str) -> None:
    """Remove a schedule from APScheduler."""
    try:
        scheduler.remove_job(schedule_id)
    except Exception:
        pass


def load_all_schedules() -> None:
    """Load all active schedules from DB into APScheduler on startup."""
    db = SessionLocal()
    try:
        active = db.query(Schedule).filter(Schedule.is_active == True).all()
        for sched in active:
            next_run = register_schedule(sched)
            if next_run:
                sched.next_run_at = next_run
        db.commit()
        log.info(f"Loaded {len(active)} active schedules")
    finally:
        db.close()


def start():
    """Start the scheduler."""
    load_all_schedules()
    scheduler.start()
    log.info("Scheduler engine started")


def shutdown():
    """Shut down the scheduler."""
    scheduler.shutdown(wait=False)
