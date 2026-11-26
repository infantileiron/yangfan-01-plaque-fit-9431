from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, new_id, utcnow


class Shift(Base):
    """A prepress shift with a fixed pool of printing minutes."""

    __tablename__ = "shifts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(100))
    budget_minutes: Mapped[float]
    actor: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Reservation(Base):
    """The minutes and paper samples one run holds inside a shift.

    ``request_key`` makes a re-sent submission idempotent: the same key returns
    the original booking instead of reserving twice. ``candidates`` stores the
    run's analysis rows (with minutes and probability) so the shift can be
    re-packed whenever another order is inserted. ``samples`` is what the shift
    currently plans to print for this run; ``started_samples`` are already being
    printed and keep their minutes through every re-plan.
    """

    __tablename__ = "reservations"
    __table_args__ = (
        UniqueConstraint("run_id", name="one_reservation_per_run"),
        UniqueConstraint("request_key", name="one_reservation_per_request"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    shift_id: Mapped[str] = mapped_column(ForeignKey("shifts.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    request_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reserved_minutes: Mapped[float] = mapped_column(default=0.0)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    required_groups: Mapped[list] = mapped_column(JSON, default=list)
    samples: Mapped[list] = mapped_column(JSON, default=list)
    started_samples: Mapped[list] = mapped_column(JSON, default=list)
    missing_groups: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
