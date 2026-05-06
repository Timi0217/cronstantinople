"""Cronstantinople — Persistent scheduling for agents and humans."""
import os
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from models import SessionLocal, Schedule, Execution, new_id
from schemas import ScheduleCreate, ScheduleUpdate, ScheduleOut, ScheduleDetail, ExecutionOut
import scheduler_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cronstantinople")

MANIFEST = {
    "schema": "chekk.agent/v1",
    "name": "cronstantinople",
    "slug": "chekk/cronstantinople",
    "description": "Persistent cron scheduler for AI agents. Create, manage, and monitor scheduled webhook jobs. Agents create a schedule once — Cronstantinople remembers and fires reliably, even across restarts.",
    "source_url": "https://github.com/Timi0217/cronstantinople",
    "framework": "fastapi",
    "entrypoints": {
        "manifest": "/.well-known/agent.json",
        "rest_base": "/",
    },
    "actions": [
        {
            "id": "create_schedule",
            "method": "POST",
            "path": "/schedules",
            "description": "Create a new scheduled job. Provide a cron expression (e.g. '0 9 * * MON' for every Monday 9am) or interval_seconds, plus a webhook_url to POST when it fires.",
            "side_effect": "mutate",
            "inputs": [
                {"name": "name", "type": "string", "required": True, "hint": "Human-readable name, e.g. 'Monday standup reminder'", "location": "body"},
                {"name": "cron_expr", "type": "string", "required": False, "hint": "Cron expression: 'minute hour day month weekday', e.g. '0 9 * * MON'", "location": "body"},
                {"name": "interval_seconds", "type": "integer", "required": False, "hint": "Run every N seconds (alternative to cron_expr)", "location": "body"},
                {"name": "webhook_url", "type": "string", "required": True, "hint": "URL to POST when the schedule fires", "location": "body"},
                {"name": "webhook_headers", "type": "object", "required": False, "hint": "Optional HTTP headers for the webhook", "location": "body"},
                {"name": "webhook_body", "type": "object", "required": False, "hint": "Optional JSON body to include in the webhook POST", "location": "body"},
                {"name": "timezone", "type": "string", "required": False, "hint": "IANA timezone, default UTC", "location": "body"},
            ],
            "response": {"content_type": "application/json", "model": "Schedule"},
        },
        {
            "id": "list_schedules",
            "method": "GET",
            "path": "/schedules",
            "description": "List all schedules with their status, next run time, and last result.",
            "side_effect": "read",
            "inputs": [],
            "response": {"content_type": "application/json", "model": "Schedule[]"},
        },
        {
            "id": "get_schedule",
            "method": "GET",
            "path": "/schedules/{schedule_id}",
            "description": "Get a single schedule with its recent execution history.",
            "side_effect": "read",
            "inputs": [
                {"name": "schedule_id", "type": "string", "required": True, "hint": "The schedule ID", "location": "path"},
            ],
            "response": {"content_type": "application/json", "model": "ScheduleDetail"},
        },
        {
            "id": "update_schedule",
            "method": "PATCH",
            "path": "/schedules/{schedule_id}",
            "description": "Update a schedule. Use is_active=false to pause, is_active=true to resume. Can also change cron_expr, webhook_url, etc.",
            "side_effect": "mutate",
            "inputs": [
                {"name": "schedule_id", "type": "string", "required": True, "hint": "The schedule ID", "location": "path"},
                {"name": "name", "type": "string", "required": False, "location": "body"},
                {"name": "cron_expr", "type": "string", "required": False, "location": "body"},
                {"name": "interval_seconds", "type": "integer", "required": False, "location": "body"},
                {"name": "webhook_url", "type": "string", "required": False, "location": "body"},
                {"name": "webhook_headers", "type": "object", "required": False, "location": "body"},
                {"name": "webhook_body", "type": "object", "required": False, "location": "body"},
                {"name": "timezone", "type": "string", "required": False, "location": "body"},
                {"name": "is_active", "type": "boolean", "required": False, "hint": "Set false to pause, true to resume", "location": "body"},
            ],
            "response": {"content_type": "application/json", "model": "Schedule"},
        },
        {
            "id": "delete_schedule",
            "method": "DELETE",
            "path": "/schedules/{schedule_id}",
            "description": "Permanently delete a schedule and all its execution history.",
            "side_effect": "irreversible",
            "inputs": [
                {"name": "schedule_id", "type": "string", "required": True, "hint": "The schedule ID", "location": "path"},
            ],
            "response": {"content_type": "application/json"},
        },
        {
            "id": "trigger_schedule",
            "method": "POST",
            "path": "/schedules/{schedule_id}/trigger",
            "description": "Immediately fire a schedule's webhook (manual trigger). Useful for testing.",
            "side_effect": "mutate",
            "inputs": [
                {"name": "schedule_id", "type": "string", "required": True, "hint": "The schedule ID", "location": "path"},
            ],
            "response": {"content_type": "application/json", "model": "Execution"},
        },
    ],
    "requires": {"auth": False, "payment": False},
    "session": {"supported": False},
    "limits": {"requests_per_minute": 60},
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def schedule_to_dict(s: Schedule) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description or "",
        "cron_expr": s.cron_expr,
        "interval_seconds": s.interval_seconds,
        "webhook_url": s.webhook_url,
        "webhook_headers": json.loads(s.webhook_headers) if isinstance(s.webhook_headers, str) else (s.webhook_headers or {}),
        "webhook_body": json.loads(s.webhook_body) if isinstance(s.webhook_body, str) else (s.webhook_body or {}),
        "timezone": s.timezone or "UTC",
        "is_active": s.is_active,
        "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
        "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "created_by": s.created_by or "human",
    }


def execution_to_dict(e: Execution) -> dict:
    return {
        "id": e.id,
        "schedule_id": e.schedule_id,
        "triggered_at": e.triggered_at.isoformat() if e.triggered_at else None,
        "status": e.status,
        "response_code": e.response_code,
        "response_body": e.response_body or "",
        "duration_ms": e.duration_ms or 0,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler_engine.start()
    log.info("Cronstantinople started")
    yield
    scheduler_engine.shutdown()
    log.info("Cronstantinople stopped")


app = FastAPI(title="Cronstantinople", description="Persistent cron scheduler for agents", lifespan=lifespan)


# ── Agent manifest ──────────────────────────────────────────
@app.get("/.well-known/agent.json")
async def agent_manifest():
    return JSONResponse(MANIFEST)


# ── Health ──────────────────────────────────────────────────
@app.get("/health")
async def health(db: Session = Depends(get_db)):
    count = db.query(Schedule).count()
    active = db.query(Schedule).filter(Schedule.is_active == True).count()
    return {"status": "ok", "service": "cronstantinople", "schedules": count, "active": active}


# ── CRUD ────────────────────────────────────────────────────
@app.post("/schedules", status_code=201)
async def create_schedule(body: ScheduleCreate, db: Session = Depends(get_db)):
    if not body.cron_expr and not body.interval_seconds:
        raise HTTPException(400, "Provide either cron_expr or interval_seconds")

    sched = Schedule(
        id=new_id(),
        name=body.name,
        description=body.description,
        cron_expr=body.cron_expr,
        interval_seconds=body.interval_seconds,
        webhook_url=body.webhook_url,
        webhook_headers=json.dumps(body.webhook_headers),
        webhook_body=json.dumps(body.webhook_body),
        timezone=body.timezone,
        created_by="agent",  # could be enriched with auth later
    )
    db.add(sched)
    db.commit()
    db.refresh(sched)

    next_run = scheduler_engine.register_schedule(sched)
    if next_run:
        sched.next_run_at = next_run
        db.commit()

    return schedule_to_dict(sched)


@app.get("/schedules")
async def list_schedules(db: Session = Depends(get_db)):
    schedules = db.query(Schedule).order_by(Schedule.created_at.desc()).all()
    return [schedule_to_dict(s) for s in schedules]


@app.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str, db: Session = Depends(get_db)):
    sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not sched:
        raise HTTPException(404, "Schedule not found")

    execs = db.query(Execution).filter(Execution.schedule_id == schedule_id).order_by(Execution.triggered_at.desc()).limit(20).all()

    result = schedule_to_dict(sched)
    result["recent_executions"] = [execution_to_dict(e) for e in execs]
    return result


@app.patch("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, body: ScheduleUpdate, db: Session = Depends(get_db)):
    sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not sched:
        raise HTTPException(404, "Schedule not found")

    updates = body.model_dump(exclude_unset=True)
    for key, val in updates.items():
        if key in ("webhook_headers", "webhook_body") and isinstance(val, dict):
            setattr(sched, key, json.dumps(val))
        else:
            setattr(sched, key, val)

    db.commit()
    db.refresh(sched)

    if sched.is_active:
        next_run = scheduler_engine.register_schedule(sched)
        if next_run:
            sched.next_run_at = next_run
            db.commit()
    else:
        scheduler_engine.unregister_schedule(schedule_id)
        sched.next_run_at = None
        db.commit()

    return schedule_to_dict(sched)


@app.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, db: Session = Depends(get_db)):
    sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not sched:
        raise HTTPException(404, "Schedule not found")

    scheduler_engine.unregister_schedule(schedule_id)
    db.query(Execution).filter(Execution.schedule_id == schedule_id).delete()
    db.delete(sched)
    db.commit()
    return {"ok": True, "deleted": schedule_id}


@app.post("/schedules/{schedule_id}/trigger")
async def trigger_schedule(schedule_id: str, db: Session = Depends(get_db)):
    sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not sched:
        raise HTTPException(404, "Schedule not found")

    # Fire synchronously so the caller gets the result
    import threading
    result = {}

    def _fire():
        scheduler_engine.fire_webhook(schedule_id)

    t = threading.Thread(target=_fire)
    t.start()
    t.join(timeout=35)

    # Return the most recent execution
    db.expire_all()
    latest = db.query(Execution).filter(Execution.schedule_id == schedule_id).order_by(Execution.triggered_at.desc()).first()
    if latest:
        return execution_to_dict(latest)
    return {"ok": True, "message": "Trigger fired"}


@app.get("/schedules/{schedule_id}/executions")
async def list_executions(schedule_id: str, limit: int = 50, db: Session = Depends(get_db)):
    sched = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not sched:
        raise HTTPException(404, "Schedule not found")

    execs = db.query(Execution).filter(Execution.schedule_id == schedule_id).order_by(Execution.triggered_at.desc()).limit(limit).all()
    return [execution_to_dict(e) for e in execs]


# ── Static UI ───────────────────────────────────────────────
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    index = os.path.join(static_dir, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return {"service": "cronstantinople", "docs": "/docs", "manifest": "/.well-known/agent.json"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
