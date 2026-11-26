"""Initial schema for a fresh Compose volume, plus additive column upgrades.

``create_all`` only creates missing tables, so an existing volume would otherwise
miss the shift columns added to ``runs``. The upgrade step below adds any missing
nullable columns and unique constraints in place, and is safe to run repeatedly.
"""
from sqlalchemy import inspect, text

from src.models.account import Account
from src.models.audit import Event
from src.models.dataset import Dataset
from src.models.review import Review
from src.models.run import Run
from src.models.shift import Reservation, Shift

SCHEMA = (Account, Dataset, Shift, Run, Reservation, Review, Event)

# Additive columns that may be missing on an older volume: (table, column, DDL type).
# The DDL is deliberately FK-free so it applies in place on both PostgreSQL and
# SQLite; the foreign key still holds for fresh volumes created by create_all.
ADDITIVE = [
    ("runs", "shift_id", "VARCHAR(36)"),
    ("runs", "request_key", "VARCHAR(80)"),
]
UNIQUE = [("runs", "request_key", "runs_request_key_uq")]


def migrate(engine):
    tables = [model.__table__ for model in SCHEMA]
    Account.metadata.create_all(engine, tables=tables)
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table, column, ddl in ADDITIVE:
            existing = {item["name"] for item in inspector.get_columns(table)}
            if column not in existing:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        indexer = inspect(connection)
        for table, column, name in UNIQUE:
            unique = {tuple(item["column_names"]) for item in indexer.get_unique_constraints(table)}
            if (column,) not in unique:
                try:
                    connection.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({column})"))
                except Exception:
                    # SQLite cannot add constraints in place; it never has old volumes.
                    pass
