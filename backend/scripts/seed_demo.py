import json

from sqlalchemy import func, select

from migrations.bootstrap import migrate
from src.business.demo import demo_rows
from src.business.profile import PROFILE
from src.config.settings import Session, engine
from src.constants.workflow import Role
from src.models.account import Account
from src.models.dataset import Dataset
from src.models.shift import Shift
from src.services.datasets import save_in
from src.utils.csv_data import parse_csv, render_csv
from src.utils.passwords import hash_password

PASSWORD = "Yangfan-demo-2026"
SEED_SHIFT = "2026-11-26 早班"


def seed():
    columns = PROFILE["columns"]
    rows = parse_csv(render_csv(demo_rows(), [column["key"] for column in columns]), columns)
    with Session.begin() as db:
        for role in Role:
            email = f"{role.value}@demo.local"
            if db.get(Account, email) is None:
                db.add(Account(email=email, role=role.value, password_hash=hash_password(PASSWORD)))
        db.flush()
        item, created = save_in(db, PROFILE["seed_name"], rows, "seed", audit=False)
        shift_created = False
        if db.scalar(select(Shift).where(Shift.key == SEED_SHIFT)) is None:
            db.add(Shift(key=SEED_SHIFT, label=SEED_SHIFT,
                         budget_minutes=float(PROFILE["shift"]["default"]), actor="seed"))
            shift_created = True
        return {
            "accounts": db.scalar(select(func.count()).select_from(Account)),
            "datasets": db.scalar(select(func.count()).select_from(Dataset)),
            "shifts": db.scalar(select(func.count()).select_from(Shift)),
            "rows": len(item.rows), "checksum": item.checksum, "created": created,
            "shift_created": shift_created,
        }


if __name__ == "__main__":
    migrate(engine)
    print(json.dumps(seed(), ensure_ascii=False))
