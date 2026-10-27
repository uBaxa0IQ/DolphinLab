"""Bounded, code-free analytical payloads and independent verification evidence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Column(Payload):
    name: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)
    type: Literal["string", "number"]
    unit: str = Field(default="", max_length=80)


class TableData(Payload):
    columns: list[Column] = Field(min_length=1, max_length=30)
    rows: list[list[str | float | None]] = Field(max_length=500)
    selection: str = Field(min_length=1, max_length=4000)
    sample_size: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_table(self):
        if len({c.name for c in self.columns}) != len(self.columns):
            raise ValueError("Column names must be unique")
        if len(self.rows) * len(self.columns) > 5000:
            raise ValueError("Prepare a smaller view: at most 5000 cells")
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError("Row width differs from schema")
            for value, column in zip(row, self.columns):
                if value is not None and (
                    (column.type == "number" and not isinstance(value, (int, float)))
                    or (column.type == "string" and not isinstance(value, str))
                ):
                    raise ValueError("Cell type differs from column schema")
                if isinstance(value, str) and len(value) > 200:
                    raise ValueError("Cell exceeds prepared-view limit")
        return self


class MetricData(Payload):
    value: float
    unit: str = Field(min_length=1, max_length=80)
    selection: str = Field(min_length=1, max_length=4000)
    sample_size: int = Field(ge=0)


class FindingData(Payload):
    text: str = Field(min_length=1, max_length=8000)
    limitations: list[str] = Field(default_factory=list, max_length=20)


class ChartData(Payload):
    type: Literal["bar", "line", "scatter"]
    table_id: str = Field(min_length=1, max_length=120)
    x: str = Field(min_length=1, max_length=120)
    y: str = Field(min_length=1, max_length=120)
    x_label: str = Field(min_length=1, max_length=200)
    y_label: str = Field(min_length=1, max_length=200)
    caption: str = Field(min_length=1, max_length=4000)


class ReportBlock(Payload):
    result_id: str = Field(min_length=1, max_length=120)
    caption: str = Field(default="", max_length=2000)
    required: bool = True


class ReportData(Payload):
    summary: str = Field(min_length=1, max_length=8000)
    blocks: list[ReportBlock] = Field(min_length=1, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=20)


class Check(Payload):
    name: str = Field(min_length=1, max_length=1000)
    passed: bool
    detail: str = Field(min_length=1, max_length=2000)


class VerificationData(Payload):
    result_id: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["verified", "refuted", "inconclusive"]
    checks: list[Check] = Field(min_length=1, max_length=30)
    conclusion: str = Field(min_length=1, max_length=8000)

    @model_validator(mode="after")
    def consistent_verdict(self):
        if self.status == "verified" and not all(c.passed for c in self.checks):
            raise ValueError("A failed check cannot yield verified")
        if self.status == "refuted" and all(c.passed for c in self.checks):
            raise ValueError("Refutation needs a failed check")
        return self


PAYLOADS = {
    "dataset": TableData,
    "table": TableData,
    "metric": MetricData,
    "finding": FindingData,
    "chart": ChartData,
    "report": ReportData,
}
