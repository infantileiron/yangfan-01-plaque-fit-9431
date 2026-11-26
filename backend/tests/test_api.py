"""End-to-end HTTP tests: shifts, admission, retry and handoff through the API."""
import pytest
from fastapi.testclient import TestClient

from migrations.bootstrap import migrate
from src.business.demo import demo_rows
from src.business.profile import PROFILE
from src.config.settings import Session, engine
from src.models.account import Account
from src.models.dataset import Dataset
from src.models.run import Run
from src.models.shift import Reservation, Shift
from src.services.executor import claim, execute
from src.utils.csv_data import render_csv
from src.utils.passwords import hash_password


@pytest.fixture()
def client():
    migrate(engine)
    with Session.begin() as db:
        for table in (Reservation, Run, Shift, Dataset):
            db.query(table).delete()
        for role in ("analyst", "reviewer", "viewer"):
            email = f"{role}@demo.local"
            if db.get(Account, email) is None:
                db.add(Account(email=email, role=role, password_hash=hash_password("pw")))
    from src.main import app
    app_ = TestClient(app)
    tokens = {}
    for role in ("analyst", "reviewer", "viewer"):
        response = app_.post("/api/auth/login", json={"email": f"{role}@demo.local", "password": "pw"})
        tokens[role] = {"Authorization": "Bearer " + response.json()["token"]}
    return app_, tokens


def make_dataset(client, auth, name="demo"):
    csv = render_csv(demo_rows(), [column["key"] for column in PROFILE["columns"]])
    response = client.post("/api/datasets", json={"name": name, "csv": csv}, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def make_shift(client, auth, key, budget):
    response = client.post("/api/shifts", json={"key": key, "budget_minutes": budget}, headers=auth)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def submit(client, auth, dataset, shift, label, key):
    return client.post("/api/runs", json={
        "dataset_id": dataset, "shift_id": shift, "label": label, "parameter": 0.6, "request_key": key,
    }, headers=auth)


def test_full_shift_flow(client):
    app, tokens = client
    analyst, reviewer, viewer = tokens["analyst"], tokens["reviewer"], tokens["viewer"]
    dataset = make_dataset(app, analyst)
    shift = make_shift(app, analyst, "早班", 200)

    first = submit(app, analyst, dataset, shift, "一", "flow-key-0001")
    assert first.status_code == 202, first.text
    assert first.json()["created"] is True
    assert first.json()["plan"]["scheduled_minutes"] <= 200.0001

    # A duplicate submission returns the same order without adding minutes.
    repeat = submit(app, analyst, dataset, shift, "一", "flow-key-0001").json()
    assert repeat["created"] is False and repeat["id"] == first.json()["id"]

    execute(claim())
    run1 = app.get(f"/api/runs/{first.json()['id']}", headers=viewer).json()
    assert run1["status"] == "succeeded"
    booked = set(run1["result"]["plan"]["scheduled"])

    # Insert a second order after the first has finished printing.
    second = submit(app, analyst, dataset, shift, "二", "flow-key-0002")
    assert second.status_code == 202, second.text
    execute(claim())

    run1_after = app.get(f"/api/runs/{first.json()['id']}", headers=viewer).json()
    assert set(run1_after["result"]["plan"]["scheduled"]) == booked, "finished order keeps its samples"
    detail = app.get(f"/api/shifts/{shift}", headers=viewer).json()
    assert detail["used_minutes"] <= 200.0001
    assert abs(detail["used_minutes"] - sum(r["minutes"] for r in detail["runs"])) < 0.05

    # Handoff only lists samples the shift booked for this order.
    approved = [row["sample_id"] for row in run1_after["result"]["rows"]]
    signed = app.post(f"/api/runs/{first.json()['id']}/review",
                      json={"decision": "approve", "note": "复核通过", "samples": approved}, headers=reviewer)
    assert signed.status_code == 201
    handoff = app.get(f"/api/runs/{first.json()['id']}/handoff.csv", headers=reviewer)
    exported = {line.split(",")[0] for line in handoff.text.strip().splitlines()[1:]}
    assert exported == booked


def test_viewer_cannot_submit_or_open_shift(client):
    app, tokens = client
    dataset = make_dataset(app, tokens["analyst"])
    shift = make_shift(app, tokens["analyst"], "早班", 120)
    denied = submit(app, tokens["viewer"], dataset, shift, "观察", "viewer-key-01")
    assert denied.status_code == 403
    assert app.post("/api/shifts", json={"key": "x班", "budget_minutes": 60},
                    headers=tokens["viewer"]).status_code == 403


def test_shift_budget_is_immutable(client):
    app, tokens = client
    make_shift(app, tokens["analyst"], "早班", 90)
    clash = app.post("/api/shifts", json={"key": "早班", "budget_minutes": 120}, headers=tokens["analyst"])
    assert clash.status_code == 409


def test_missing_unseen_group_is_reported_at_admission(client):
    app, tokens = client
    analyst = tokens["analyst"]
    # Build a dataset with a second unseen pending combination and price both so
    # high that a 30-minute shift cannot fit them together.
    rows = demo_rows()
    from src.business.demo import sample
    extra = sample("LY-P09", "待校", "LY-T02", "中文/芬兰文", 75, 3, 0.9, None)
    extra["est_minutes"] = 29.0
    rows.append(extra)
    for row in rows:
        if row["sample_id"] == "LY-P08":
            row["est_minutes"] = 29.0
    csv = render_csv(rows, [column["key"] for column in PROFILE["columns"]])
    dataset = app.post("/api/datasets", json={"name": "tight", "csv": csv}, headers=analyst).json()["id"]
    shift = make_shift(app, analyst, "紧班", 30)
    body = submit(app, analyst, dataset, shift, "紧", "tight-key-01").json()
    assert len(body["plan"]["missing_groups"]) == 1
    assert "工时不足" in body["summary"]
    assert any("未排入的组合" in note for note in body["notes"])
