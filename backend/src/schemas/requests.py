from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints
from typing import Annotated

from src.constants.workflow import Decision

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Request):
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class Import(Request):
    name: Name
    csv: Annotated[str, StringConstraints(min_length=1, max_length=1_000_000)]


class RunRequest(Request):
    dataset_id: Annotated[str, StringConstraints(min_length=36, max_length=36)]
    shift_id: Annotated[str, StringConstraints(min_length=36, max_length=36)]
    label: Name
    parameter: FiniteFloat
    request_key: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=8, max_length=80)] = None


class ShiftRequest(Request):
    key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    label: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] = None
    budget_minutes: Annotated[FiniteFloat, Field(ge=30, le=1440)]


class StartRequest(Request):
    samples: Annotated[list[Annotated[str, StringConstraints(min_length=1, max_length=120)]], Field(min_length=1, max_length=500)]


class ReviewRequest(Request):
    decision: Decision
    samples: Annotated[list[Annotated[str, StringConstraints(min_length=1, max_length=120)]], Field(max_length=500)]
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=1000)]
