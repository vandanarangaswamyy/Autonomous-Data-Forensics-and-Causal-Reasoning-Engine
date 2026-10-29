from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

NonEmpty = Annotated[str, Field(min_length=1)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1


class InvestigationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Investigation(Contract):
    id: UUID = Field(default_factory=uuid4)
    title: NonEmpty
    status: InvestigationStatus = InvestigationStatus.PENDING
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def ordered_times(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self


class Evidence(Contract):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    source_uri: NonEmpty
    content_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    summary: NonEmpty
    observed_at: AwareDatetime
    recorded_at: AwareDatetime
    tool_execution_id: UUID | None = None


class Hypothesis(Contract):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    statement: NonEmpty
    status: Literal["proposed", "supported", "rejected"] = "proposed"
    evidence_ids: tuple[UUID, ...] = ()
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_evidence(self) -> Self:
        if self.status != "proposed" and not self.evidence_ids:
            raise ValueError("Supported or rejected hypotheses require evidence references")
        return self


class ToolExecution(Contract):
    id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    tool_name: NonEmpty
    requested_at: AwareDatetime
    status: Literal["requested", "denied", "running", "succeeded", "failed", "cancelled"]
    policy_decision: Literal["pending", "allow", "deny"]
    timeout_seconds: float = Field(gt=0, le=300, allow_inf_nan=False)
    result_limit: int = Field(gt=0, le=10000)
    input_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    evidence_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def require_policy(self) -> Self:
        if self.status in {"running", "succeeded", "failed"} and self.policy_decision != "allow":
            raise ValueError("Execution requires an allow policy decision")
        if self.status == "denied" and self.policy_decision != "deny":
            raise ValueError("Denied execution requires a deny decision")
        return self


class AgentState(Contract):
    investigation_id: UUID
    checkpoint_id: UUID | None = None
    revision: int = Field(default=0, ge=0)
    phase: Literal["queued", "investigating", "reporting", "finished"] = "queued"
    evidence_ids: tuple[UUID, ...] = ()
    hypothesis_ids: tuple[UUID, ...] = ()
    tool_execution_ids: tuple[UUID, ...] = ()
    cancellation_requested: bool = False
    context: dict[str, JsonValue] = Field(default_factory=dict)
