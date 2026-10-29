"""Public telemetry only. No injection IDs, oracle labels or expected causes."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter


class EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    event_id: UUID
    run_id: UUID
    occurred_at: AwareDatetime


class PipelineEvent(EventBase):
    event_type: Literal["pipeline.execution"] = "pipeline.execution"
    pipeline_id: str
    status: Literal["success", "degraded", "failed"]
    input_rows: int = Field(ge=0)
    output_rows: int = Field(ge=0)
    quality: dict[str, float]


class SchemaEvent(EventBase):
    event_type: Literal["schema.change"] = "schema.change"
    dataset: str
    version: int = Field(ge=1)
    fields: tuple[str, ...]


class QualityEvent(EventBase):
    event_type: Literal["quality.alert"] = "quality.alert"
    dataset: str
    check: str
    observed: float
    threshold: float


class TelemetryEvent(EventBase):
    event_type: Literal["operational.telemetry"] = "operational.telemetry"
    component: str
    code: str
    severity: Literal["info", "warning", "error"]
    measurements: dict[str, float]


SimulatorEvent = Annotated[
    PipelineEvent | SchemaEvent | QualityEvent | TelemetryEvent, Field(discriminator="event_type")
]
EVENT_ADAPTER: TypeAdapter[SimulatorEvent] = TypeAdapter(SimulatorEvent)  # type: ignore[arg-type]
