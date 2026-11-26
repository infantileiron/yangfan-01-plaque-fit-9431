"""Shift admission and minute reservations.

A shift is a fixed pool of prepress minutes shared by every calculation order
opened during that shift. Admission runs under a row lock on the shift, so two
orders submitted at the same time can never together book more minutes than the
shift has.

Each order keeps exactly one reservation (``one_reservation_per_run``). Retrying
a failed order reuses that reservation instead of taking a second slice of the
shift, and a client-supplied ``request_key`` makes a re-sent submission return
the original order, so a retry never double-books minutes.

Every admission re-plans the whole shift from the stored candidate lists of its
orders. Samples that already started keep their booked minutes; the rest are
re-packed so the crew keeps working and the expected number of discovered
overflows is as high as the pool allows. Unseen layout / language combinations
are covered before any spare minutes go to filling probability.
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.business.algorithm import analyze
from src.business.scheduling import PlanItem, reschedule
from src.config.settings import Session
from src.models.base import utcnow
from src.models.run import Run
from src.models.shift import Reservation, Shift
from src.schemas.resources import shift_view
from src.services.audit import record

ACTIVE = {"queued", "running", "succeeded"}


def candidates_from(result):
    """Scheduler items and unseen combinations from an analysis result."""
    items = [
        PlanItem(
            sample_id=row["sample_id"],
            minutes=float(row["minutes"]),
            score=float(row["probability"]) / 100.0,
            group_key=row["template"],
        )
        for row in result["rows"]
    ]
    required = list(result.get("plan", {}).get("required_groups", []))
    return items, required


def _item_record(item):
    return {"sample_id": item.sample_id, "minutes": item.minutes, "score": item.score, "group": item.group_key}


def used_minutes(db, shift_id):
    total = 0.0
    for reservation in db.scalars(select(Reservation).where(Reservation.shift_id == shift_id)):
        total += float(reservation.reserved_minutes or 0.0)
    return total


def open_shift(db, key, label, budget, actor):
    budget = float(budget)
    shift = db.scalar(select(Shift).where(Shift.key == key).with_for_update())
    if shift is not None:
        if abs(float(shift.budget_minutes) - budget) > 1e-9:
            raise HTTPException(
                409, f"班次「{key}」工时已定为 {shift.budget_minutes:g} 分钟，不能中途改动。请另开新班次。")
        return shift
    shift = Shift(key=key, label=label, budget_minutes=budget, actor=actor)
    try:
        with db.begin_nested():
            db.add(shift)
            db.flush()
    except IntegrityError:
        # Another request opened the same shift concurrently; reuse it.
        shift = db.scalar(select(Shift).where(Shift.key == key).with_for_update())
        if shift is None:
            raise
        if abs(float(shift.budget_minutes) - budget) > 1e-9:
            raise HTTPException(409, f"班次「{key}」工时已定为 {shift.budget_minutes:g} 分钟，不能中途改动。")
    return shift


def require(db, shift_id):
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise HTTPException(404, "班次不存在。")
    return shift


def create(key, label, budget, actor):
    with Session.begin() as db:
        shift = open_shift(db, key, label, budget, actor)
        record(db, actor, "shift_opened", shift.id)
        return shift_view(shift, used_minutes(db, shift.id))


def list_shifts():
    with Session.begin() as db:
        shifts = list(db.scalars(select(Shift).order_by(Shift.created_at.desc(), Shift.id.desc())))
        return [shift_view(shift, used_minutes(db, shift.id)) for shift in shifts]


def details(shift_id):
    with Session.begin() as db:
        shift = require(db, shift_id)
        reservations = list(db.scalars(
            select(Reservation).where(Reservation.shift_id == shift_id).order_by(Reservation.created_at)))
        missing = sorted({group for reservation in reservations for group in reservation.missing_groups or []})
        runs = []
        for reservation in reservations:
            run = db.get(Run, reservation.run_id)
            runs.append({
                "run_id": reservation.run_id, "label": run.label if run else None,
                "status": run.status if run else None,
                "minutes": round(float(reservation.reserved_minutes or 0), 1),
                "samples": list(reservation.samples or []),
                "started": list(reservation.started_samples or []),
                "required_groups": list(reservation.required_groups or []),
                "missing_groups": list(reservation.missing_groups or []),
            })
        return {**shift_view(shift, used_minutes(db, shift.id)), "runs": runs, "missing_groups": missing}


def reservation_for(db, run_id):
    return db.scalar(select(Reservation).where(Reservation.run_id == run_id))


def find_request(db, request_key):
    if not request_key:
        return None
    return db.scalar(select(Reservation).where(Reservation.request_key == request_key))


def _active(db, shift):
    reservations = []
    for reservation in db.scalars(select(Reservation).where(Reservation.shift_id == shift.id)):
        run = db.get(Run, reservation.run_id)
        if run is None or run.status not in ACTIVE:
            continue
        reservations.append(reservation)
    return reservations


def replan(db, shift, anchor_run=None):
    """Re-pack every active order in the shift; returns (missing groups, anchored).

    Paper samples are pooled across orders under a run-scoped key, so an order is
    planned as a whole and two orders never collide on a sample id. Samples that
    already started are pinned. The pack is first solved for the highest expected
    overflow; only orders that would then get no sample at all receive a forced
    foothold, so an insertion can never silently starve an order.
    """
    # Serialise against admission and other re-plans: every change to a shift's
    # bookings happens while holding its row lock.
    db.scalar(select(Shift).where(Shift.id == shift.id).with_for_update())
    reservations = _active(db, shift)
    pool = []
    owner = {}
    started = set()
    required = []
    per_run = {reservation.run_id: [] for reservation in reservations}
    for reservation in reservations:
        run = db.get(Run, reservation.run_id)
        done = run is not None and run.status == "succeeded"
        for entry in reservation.candidates or []:
            key = f"{reservation.run_id}:{entry['sample_id']}"
            item = PlanItem(key, float(entry["minutes"]), float(entry["score"]), entry.get("group"))
            pool.append(item)
            per_run[reservation.run_id].append(item)
            owner[key] = (reservation, entry["sample_id"])
        for sample in reservation.started_samples or []:
            started.add(f"{reservation.run_id}:{sample}")
        if done:
            # A finished order has already printed its samples; they stay booked.
            for sample in reservation.samples or []:
                started.add(f"{reservation.run_id}:{sample}")
        for group in reservation.required_groups or []:
            if group not in required:
                required.append(group)

    result = reschedule(pool, shift.budget_minutes, required, started)
    starved = [run_id for run_id, items in per_run.items()
               if items and not any(item.sample_id in result.selected for item in items)]
    if starved:
        footholds = []
        for run_id in starved:
            items = per_run[run_id]
            pinned = [item for item in items if item.sample_id in started]
            pick = pinned or [min(items, key=lambda item: (item.minutes, -item.score, item.sample_id))]
            footholds.extend(item.sample_id for item in pick)
        result = reschedule(pool, shift.budget_minutes, required, started, footholds)

    assigned = {reservation.run_id: [] for reservation in reservations}
    for key in result.ordered:
        reservation, sample_id = owner[key]
        assigned[reservation.run_id].append(sample_id)
    if anchor_run is not None and not assigned.get(anchor_run):
        # Even with a foothold the new order fits nothing: refuse the admission.
        return result.missing_groups, False
    by_key = {item.sample_id: item for item in pool}
    missing = result.missing_groups
    for reservation in reservations:
        samples = assigned[reservation.run_id]
        reservation.samples = samples
        reservation.reserved_minutes = round(sum(
            by_key[f"{reservation.run_id}:{sample}"].minutes for sample in samples), 2)
        reservation.missing_groups = [group for group in reservation.required_groups or [] if group in missing]
        reservation.updated_at = utcnow()
    db.flush()
    return missing, True


def admit(db, run, request_key=None):
    """Reserve minutes for a run inside a shift-locked transaction.

    Must be called inside ``Session.begin()``; raises ``HTTPException`` (and lets
    the caller roll back) when the shift cannot fit the order.
    """
    shift = db.scalar(select(Shift).where(Shift.id == run.shift_id).with_for_update())
    if shift is None:
        raise HTTPException(404, "班次不存在。")
    try:
        preview = analyze(_dataset_rows(db, run.dataset_id), run.parameter, int(shift.budget_minutes))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    items, required = candidates_from(preview)

    reservation = reservation_for(db, run.id)
    if reservation is None:
        reservation = Reservation(shift_id=shift.id, run_id=run.id, request_key=request_key)
        db.add(reservation)
    reservation.shift_id = shift.id
    reservation.request_key = request_key
    reservation.candidates = [_item_record(item) for item in items]
    reservation.required_groups = required
    reservation.started_samples = list(dict.fromkeys(reservation.started_samples or []))
    db.flush()

    missing, anchored = replan(db, shift, anchor_run=run.id)
    if not anchored or (not reservation.samples and not reservation.started_samples):
        raise HTTPException(409, "本班工时不足以排入这张计算单的任何纸样，请另开班次。")
    preview["plan"].update({
        "scheduled": list(reservation.samples),
        "missing_groups": [group for group in required if group in missing],
        "covered_groups": sorted(set(required) - set(missing)),
        "scheduled_minutes": round(float(reservation.reserved_minutes or 0), 1),
        "idle_minutes": round(max(0.0, float(shift.budget_minutes) - used_minutes(db, shift.id)), 1),
    })
    return preview


def _dataset_rows(db, dataset_id):
    from src.models.dataset import Dataset
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, "数据版本不存在。")
    return dataset.rows


def settle_in(db, run_id):
    """Pin a finished run to the samples the shift currently books for it.

    While the order ran, further insertions may have re-packed the shift; those
    re-plans already honoured this order's started samples and skip finished
    orders entirely. Settling just re-derives its minutes from the current sample
    list so the stored result and handoff match the shift exactly.
    """
    reservation = reservation_for(db, run_id)
    if reservation is None:
        return None
    known = {entry["sample_id"] for entry in reservation.candidates or []}
    final = [sample for sample in reservation.samples or [] if sample in known]
    reservation.samples = final
    reservation.reserved_minutes = round(sum(
        float(entry["minutes"]) for entry in reservation.candidates or [] if entry["sample_id"] in set(final)), 2)
    reservation.updated_at = utcnow()
    db.flush()
    shift = db.get(Shift, reservation.shift_id)
    required = set(reservation.required_groups or [])
    return {
        "samples": list(reservation.samples or []),
        "minutes": round(float(reservation.reserved_minutes or 0), 1),
        "missing_groups": list(reservation.missing_groups or []),
        "required_groups": list(reservation.required_groups or []),
        "covered_groups": sorted(required - set(reservation.missing_groups or [])),
        "shift_minutes": int(float(shift.budget_minutes)) if shift else None,
        "idle_minutes": round(max(0.0, float(shift.budget_minutes) - used_minutes(db, shift.id)), 1) if shift else 0.0,
    }


def release_in(db, run_id):
    """Release a run's minutes inside an existing transaction, then re-pack."""
    reservation = reservation_for(db, run_id)
    if reservation is None:
        return
    reservation.reserved_minutes = 0.0
    reservation.samples = []
    reservation.candidates = []
    reservation.required_groups = []
    reservation.missing_groups = []
    reservation.updated_at = utcnow()
    shift = db.get(Shift, reservation.shift_id)
    if shift is not None:
        replan(db, shift)


def release(run_id, reason):
    """Free an order's minutes after a terminal failure, then re-pack the shift."""
    with Session.begin() as db:
        reservation = reservation_for(db, run_id)
        if reservation is None:
            return
        record(db, "worker", reason, run_id)
        release_in(db, run_id)


def mark_started(run_id, samples, actor):
    with Session.begin() as db:
        reservation = reservation_for(db, run_id)
        if reservation is None:
            raise HTTPException(409, "此计算单未加入任何班次。")
        valid = set(reservation.samples or [])
        chosen = list(dict.fromkeys(samples))
        if set(chosen) - valid:
            raise HTTPException(422, "开打纸样必须属于本单已排纸样。")
        reservation.started_samples = list(dict.fromkeys(list(reservation.started_samples or []) + chosen))
        reservation.updated_at = utcnow()
        record(db, actor, "samples_started", run_id)
        shift = db.get(Shift, reservation.shift_id)
        if shift is not None:
            replan(db, shift)
        return {"run_id": run_id, "started": list(reservation.started_samples or []),
                "scheduled": list(reservation.samples or []),
                "minutes": round(float(reservation.reserved_minutes or 0), 1),
                "missing_groups": list(reservation.missing_groups or [])}


def assignment(run_id):
    with Session.begin() as db:
        reservation = reservation_for(db, run_id)
        if reservation is None:
            return None
        return {"samples": list(reservation.samples or []),
                "started": list(reservation.started_samples or []),
                "minutes": round(float(reservation.reserved_minutes or 0), 1),
                "required_groups": list(reservation.required_groups or []),
                "missing_groups": list(reservation.missing_groups or [])}
