import logging

from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.business.profile import PROFILE
from src.config.settings import Session, redis
from src.constants.workflow import Stage
from src.models.run import Run
from src.schemas.resources import run_view
from src.services import datasets, shifts
from src.services.audit import record

WAKE_KEY = "workbench:wake"
logger = logging.getLogger(__name__)


def require(db, run_id):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "计算单不存在。")
    return run


def list_runs():
    with Session() as db:
        return [run_view(run) for run in db.scalars(select(Run).order_by(Run.created_at.desc(), Run.id.desc()))]


def submit(body, actor):
    control = PROFILE["parameter"]
    if not control["min"] <= body.parameter <= control["max"]:
        raise HTTPException(422, f"「{control['label']}」超出允许范围。")
    if body.request_key:
        with Session() as db:
            existing = shifts.find_request(db, body.request_key)
            if existing is not None:
                run = db.get(Run, existing.run_id)
                # A terminally failed run is re-queued in place; its reservation is
                # reused, so the retry never books a second slice of the shift.
                response = requeue(db, run, body, actor)
                db.commit()
                if response.get("requeued"):
                    _wake(response["id"])
                return response

    try:
        with Session.begin() as db:
            datasets.require(db, body.dataset_id)
            shift = shifts.require(db, body.shift_id)
            run = Run(dataset_id=body.dataset_id, label=body.label, parameter=body.parameter,
                      actor=actor, shift_id=shift.id, request_key=body.request_key)
            db.add(run)
            db.flush()
            preview = shifts.admit(db, run, body.request_key)
            record(db, actor, "run_queued", run.id)
            response = {**run_view(run), "created": True, "plan": preview["plan"],
                        "summary": preview["summary"], "notes": preview["notes"]}
    except IntegrityError:
        # A concurrent request re-sent the same key; return the original booking.
        if not body.request_key:
            raise
        with Session() as db:
            existing = shifts.find_request(db, body.request_key)
            if existing is None:
                raise
            return {**run_view(db.get(Run, existing.run_id)), "created": False}
    _wake(response["id"])
    return response


def requeue(db, run, body, actor):
    """Re-send a submission. Failed runs restart in place; others return as-is."""
    if run is None or run.status != Stage.FAILED.value:
        return {**run_view(run), "created": False}
    run.status = Stage.QUEUED.value
    run.attempts = 0
    run.error = None
    run.result = None
    run.artifact = None
    run.lease = None
    run.started_at = None
    run.finished_at = None
    db.flush()
    shifts.admit(db, run, body.request_key)
    record(db, actor, "run_requeued", run.id)
    return {**run_view(run), "created": False, "requeued": True}


def _wake(run_id):
    try:
        with redis.pipeline() as commands:
            commands.lpush(WAKE_KEY, run_id)
            commands.ltrim(WAKE_KEY, 0, 99)
            commands.execute()
    except RedisError:
        logger.warning("Redis wake signal failed for %s. Worker will poll the database.", run_id)
