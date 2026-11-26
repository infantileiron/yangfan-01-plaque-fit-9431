import pytest
from fastapi import HTTPException

from migrations.bootstrap import migrate
from src.business.algorithm import analyze
from src.business.demo import demo_rows
from src.business.profile import PROFILE
from src.config.settings import Session, engine
from src.constants.workflow import Decision, Stage
from src.models.base import utcnow
from src.models.dataset import Dataset
from src.models.run import Run
from src.models.shift import Reservation, Shift
from src.schemas.requests import RunRequest
from src.services import exports, reviews, runs, shifts
from src.services.datasets import save_in
from src.utils.csv_data import parse_csv, render_csv


@pytest.fixture()
def env():
    migrate(engine)
    with Session.begin() as db:
        for table in (Reservation, Run, Shift, Dataset):
            db.query(table).delete()
    columns = PROFILE["columns"]
    rows = parse_csv(render_csv(demo_rows(), [column["key"] for column in columns]), columns)
    with Session.begin() as db:
        dataset, _ = save_in(db, "demo", rows, "analyst", audit=False)
        shift = Shift(key="shift-a", label="A", budget_minutes=120, actor="analyst")
        db.add(shift)
        db.flush()
        return {"dataset": dataset.id, "shift": shift.id}


def queued_run(label, env, request_key=None, parameter=0.6):
    with Session.begin() as db:
        run = Run(dataset_id=env["dataset"], shift_id=env["shift"], label=label, parameter=parameter,
                  actor="analyst", request_key=request_key)
        db.add(run)
        db.flush()
        shifts.admit(db, run, request_key)
        return run.id


def used(shift_id):
    with Session.begin() as db:
        return shifts.used_minutes(db, shift_id)


def test_orders_share_one_budget(env):
    first = queued_run("一", env)
    second = queued_run("二", env)
    assert used(env["shift"]) <= 120.0001
    with Session.begin() as db:
        a, b = shifts.reservation_for(db, first), shifts.reservation_for(db, second)
        assert a.reserved_minutes > 0 and b.reserved_minutes > 0
        assert a.reserved_minutes + b.reserved_minutes <= 120.0001


def test_full_shift_rejects_new_order(env):
    # The only sample that fits is already being printed; its minutes are pinned,
    # so a second order cannot be admitted at all.
    with Session.begin() as db:
        db.get(Shift, env["shift"]).budget_minutes = 14
    first = queued_run("一", env)
    with Session.begin() as db:
        booked = shifts.reservation_for(db, first).samples
    shifts.mark_started(first, booked, "analyst")
    with pytest.raises(HTTPException) as caught:
        queued_run("二", env)
    assert caught.value.status_code == 409
    assert used(env["shift"]) <= 14.0001


def test_retry_same_request_key_does_not_double_book(env):
    body = RunRequest(dataset_id=env["dataset"], shift_id=env["shift"], label="一",
                      parameter=0.6, request_key="retry-key-0001")
    first = runs.submit(body, "analyst")
    after_first = used(env["shift"])
    second = runs.submit(body, "analyst")
    assert second["created"] is False
    assert second["id"] == first["id"]
    assert used(env["shift"]) == after_first
    with Session.begin() as db:
        assert db.query(Reservation).filter_by(run_id=first["id"]).count() == 1


def test_failed_order_releases_minutes_then_retry_rebooks_once(env):
    run_id = queued_run("一", env, request_key="k1")
    booked = used(env["shift"])
    shifts.release(run_id, "run_failed")
    assert used(env["shift"]) == 0
    with Session.begin() as db:
        shifts.admit(db, db.get(Run, run_id))
    assert 0 < used(env["shift"]) <= booked
    with Session.begin() as db:
        assert db.query(Reservation).filter_by(run_id=run_id).count() == 1


def test_started_samples_keep_minutes_on_insert_and_replan(env):
    with Session.begin() as db:
        db.get(Shift, env["shift"]).budget_minutes = 60
    run_id = queued_run("一", env)
    with Session.begin() as db:
        first = shifts.reservation_for(db, run_id).samples[0]
    shifts.mark_started(run_id, [first], "analyst")
    queued_run("二", env)
    with Session.begin() as db:
        reservation = shifts.reservation_for(db, run_id)
        assert first in reservation.samples
        assert first in reservation.started_samples
        assert shifts.used_minutes(db, env["shift"]) <= 60.0001


def test_failed_retry_requeues_in_place_without_second_booking(env):
    body = RunRequest(dataset_id=env["dataset"], shift_id=env["shift"], label="一",
                      parameter=0.6, request_key="retry-failed-1")
    first = runs.submit(body, "analyst")
    with Session.begin() as db:
        run = db.get(Run, first["id"])
        run.status = Stage.FAILED.value
        run.error = "模拟失败"
    booked = used(env["shift"])
    retried = runs.submit(body, "analyst")
    assert retried["id"] == first["id"]
    assert retried.get("requeued") is True
    assert retried["status"] == Stage.QUEUED.value
    with Session.begin() as db:
        assert db.query(Reservation).filter_by(run_id=first["id"]).count() == 1
    assert 0 < used(env["shift"]) <= booked


def test_finished_order_keeps_its_samples_when_another_is_inserted(env):
    with Session.begin() as db:
        db.get(Shift, env["shift"]).budget_minutes = 160
    first = queued_run("一", env)
    with Session.begin() as db:
        run = db.get(Run, first)
        result = analyze(db.get(Dataset, run.dataset_id).rows, run.parameter, 160)
        run.result = result
        run.status = Stage.SUCCEEDED.value
        run.finished_at = utcnow()
        booked = list(shifts.reservation_for(db, first).samples)
        shifts.settle_in(db, first)
    queued_run("二", env)
    with Session.begin() as db:
        assert set(shifts.reservation_for(db, first).samples) == set(booked)
        assert shifts.used_minutes(db, env["shift"]) <= 160.0001


def test_handoff_export_only_lists_booked_samples(env):
    with Session.begin() as db:
        db.get(Shift, env["shift"]).budget_minutes = 40
    run_id = queued_run("一", env)
    with Session.begin() as db:
        run = db.get(Run, run_id)
        result = analyze(db.get(Dataset, run.dataset_id).rows, run.parameter, 40)
        run.result = result
        run.status = Stage.SUCCEEDED.value
        run.finished_at = utcnow()
        booked = list(shifts.reservation_for(db, run_id).samples)
        shifts.settle_in(db, run_id)
    approved = [row["sample_id"] for row in result["rows"]]
    body = type("B", (), {"decision": Decision.APPROVE, "samples": approved, "note": "复核"})
    reviews.sign(run_id, body, "reviewer@demo.local")
    response = exports.signed_download(run_id)
    header, *lines = response.body.decode("utf-8-sig").strip().splitlines()
    assert header.split(",")[0] == "sample_id"
    exported = {line.split(",")[0] for line in lines}
    with Session.begin() as db:
        stored = db.get(Run, run_id).result
    assert exported == set(booked)
    assert exported == {row["sample_id"] for row in stored["rows"] if row["scheduled"] == "是"}
    assert exports.signed_download(run_id).body  # repeatable
