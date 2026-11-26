from datetime import datetime, timezone


def utc(value: datetime | None):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def dataset_view(dataset, include_rows=False):
    item = {
        "id": dataset.id, "name": dataset.name, "count": len(dataset.rows),
        "checksum": dataset.checksum, "author": dataset.author, "created_at": utc(dataset.created_at),
    }
    if include_rows:
        item["rows"] = dataset.rows
    return item


def run_view(run, include_result=False):
    item = {
        "id": run.id, "dataset_id": run.dataset_id, "label": run.label, "parameter": run.parameter,
        "shift_id": run.shift_id, "status": run.status, "error": run.error, "actor": run.actor,
        "created_at": utc(run.created_at), "started_at": utc(run.started_at),
        "finished_at": utc(run.finished_at),
    }
    if include_result:
        item["result"] = run.result
    return item


def shift_view(shift, used):
    budget = float(shift.budget_minutes)
    used = float(used or 0.0)
    return {
        "id": shift.id, "key": shift.key, "label": shift.label, "budget_minutes": budget,
        "used_minutes": round(used, 1), "remaining_minutes": round(budget - used, 1),
        "actor": shift.actor, "created_at": utc(shift.created_at),
    }


def review_view(review):
    return {
        "id": review.id, "run_id": review.run_id, "decision": review.decision,
        "samples": review.samples, "note": review.note, "reviewer": review.reviewer,
        "created_at": utc(review.created_at),
    }


def audit_view(event):
    return {"actor": event.actor, "action": event.action, "subject": event.subject, "created_at": utc(event.created_at)}
