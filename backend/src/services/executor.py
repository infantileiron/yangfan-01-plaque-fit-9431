import json
import logging
import math
from datetime import timedelta

from sqlalchemy import and_, or_, select, update

from src.business.algorithm import analyze
from src.business.profile import PROFILE
from src.config.settings import ARTIFACTS, Session
from src.constants.workflow import Stage
from src.models.base import new_id, utcnow
from src.models.dataset import Dataset
from src.models.run import Run
from src.models.shift import Reservation, Shift
from src.services import shifts
from src.services.audit import record
from src.services.exports import result_keys
from src.utils.csv_data import render_csv

log = logging.getLogger(__name__)
LEASE = timedelta(minutes=10)
MAX_ATTEMPTS = 3


def claim():
    expired = utcnow() - LEASE
    stale = and_(Run.status == Stage.RUNNING.value, Run.started_at < expired)
    available = or_(Run.status == Stage.QUEUED.value, stale)
    with Session.begin() as db:
        exhausted = db.scalars(select(Run).where(stale, Run.attempts >= MAX_ATTEMPTS).with_for_update(skip_locked=True))
        for run in exhausted:
            run.status = Stage.FAILED.value
            run.error = "后台任务多次中断。请提交新的计算单。"
            run.finished_at = utcnow()
            record(db, "worker", "run_failed", run.id)
            # Return the abandoned minutes before anything else is planned.
            shifts.release_in(db, run.id)
        run = db.scalar(
            select(Run).where(available, Run.attempts < MAX_ATTEMPTS)
            .order_by(Run.created_at, Run.id).limit(1).with_for_update(skip_locked=True)
        )
        if run is None:
            return None
        run.status = Stage.RUNNING.value
        run.lease = new_id()
        run.started_at = utcnow()
        run.attempts += 1
        dataset = db.get(Dataset, run.dataset_id)
        shift = db.get(Shift, run.shift_id) if run.shift_id else None
        reservation = db.scalar(select(Reservation).where(Reservation.run_id == run.id))
        # The shift's booking is authoritative for which samples this run may print;
        # ``None`` means the run has no shift booking to respect.
        booked = list(reservation.samples or []) if reservation is not None else None
        return (run.id, run.lease, run.parameter, dataset.rows, shift.budget_minutes if shift else 240, booked)


def apply_assignment(result, assigned):
    """Mark only the paper samples the shift actually booked for this order.

    ``assigned`` of ``None`` means the order has no shift booking, so the analysis
    plan stands; an empty list means the shift booked nothing, so nothing is marked
    as scheduled.
    """
    if assigned is None:
        return result
    booked = set(assigned)
    for row in result["rows"]:
        row["scheduled"] = "是" if row["sample_id"] in booked else "否"
    result["plan"]["scheduled"] = [row["sample_id"] for row in result["rows"] if row["sample_id"] in booked]
    return result


def refresh_plan(result, booking):
    """Rewrite the plan summary, metrics and missing-group note from the final booking."""
    plan = result["plan"]
    plan.update({
        "scheduled": booking["samples"],
        "scheduled_minutes": booking["minutes"],
        "missing_groups": booking["missing_groups"],
        "covered_groups": booking["covered_groups"],
        "required_groups": booking["required_groups"],
        "idle_minutes": booking["idle_minutes"],
    })
    if booking.get("shift_minutes") is not None:
        plan["shift_minutes"] = booking["shift_minutes"]
    expected = round(sum(row["probability"] for row in result["rows"] if row["scheduled"] == "是") / 100.0, 2)
    plan["expected_overflows"] = expected
    missing = booking["missing_groups"]
    summary = result["summary"].split("；本班排入")[0].split("；工时不足")[0]
    summary += f"；本班排入 {len(booking['samples'])} 张"
    if missing:
        summary += f"；工时不足，缺 {len(missing)} 组"
    result["summary"] = summary
    for metric in result["metrics"]:
        if metric["label"] == "已排耗时":
            metric["value"] = booking["minutes"]
        elif metric["label"] == "预计查出溢出":
            metric["value"] = expected
        elif metric["label"] == "本班工时" and booking.get("shift_minutes") is not None:
            metric["value"] = booking["shift_minutes"]
    result["notes"] = [note for note in result["notes"] if not note.startswith("本班工时不足，未排入的组合：")]
    if missing:
        result["notes"].append("本班工时不足，未排入的组合：" + "、".join(missing) + "。")
    return result


def verify_result(result):
    if not result["rows"]:
        raise ValueError("当前参数没有得到可交接的结果。请检查数据并调整参数。")
    samples = set()
    chart = PROFILE["chart"]["key"]
    for row in result["rows"]:
        if not set(result_keys()).issubset(row) or row["sample_id"] in samples:
            raise ValueError("算法结果包含重复样本或缺少导出列。")
        if not isinstance(row[chart], (float, int)) or not math.isfinite(row[chart]):
            raise ValueError("算法结果包含无效的图表数值。")
        samples.add(row["sample_id"])
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result


def execute(job):
    run_id, lease, parameter, rows, shift_minutes, assigned = job
    artifact = None
    result = None
    error = None
    try:
        result = verify_result(analyze(rows, parameter, int(shift_minutes)))
        result = apply_assignment(result, assigned)
        artifact = f"{run_id}-{lease}.csv"
        write_artifact(artifact, result)
    except ValueError as exc:
        error = str(exc)
    except Exception:
        log.exception("Run %s failed", run_id)
        error = "计算或导出失败。请检查输入并重新提交。"
    if error:
        result = None
    stage = Stage.FAILED.value if error else Stage.SUCCEEDED.value
    committed = False
    with Session.begin() as db:
        change = db.execute(update(Run).where(Run.id == run_id, Run.lease == lease, Run.status == Stage.RUNNING.value).values(
            status=stage, result=result, artifact=artifact if not error else None, error=error, finished_at=utcnow(),
        ))
        if change.rowcount:
            record(db, "worker", f"run_{stage}", run_id)
            if error:
                # A terminally failed order hands its minutes back to the shift.
                shifts.release_in(db, run_id)
            elif result is not None:
                # A finished order keeps exactly the samples the shift books for it.
                booked = shifts.settle_in(db, run_id)
                final = apply_assignment(result, booked["samples"]) if booked else result
                if booked:
                    final = refresh_plan(final, booked)
                db.execute(update(Run).where(Run.id == run_id).values(result=final))
                write_artifact(artifact, final)
            committed = True
    if artifact and (error or not committed):
        (ARTIFACTS / artifact).unlink(missing_ok=True)


def write_artifact(artifact, result):
    path = ARTIFACTS / artifact
    temporary = path.with_suffix(".tmp")
    temporary.write_text("﻿" + render_csv(result["rows"], result_keys(), protect=True), encoding="utf-8")
    temporary.replace(path)
