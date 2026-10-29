"""Oracle storage. Mount this only into the evaluator, never investigation runtimes."""

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4, uuid5

from atlas_database.schema import customers
from atlas_simulator.domain import FaultConfig, GenerationConfig
from atlas_simulator.engine import Simulator
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import func, select


class FaultRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fault_id: UUID
    config: FaultConfig
    injected_at: AwareDatetime
    injection_timestamps: list[AwareDatetime] = Field(default_factory=list)
    root_cause: str
    status: Literal["pending", "active", "reset"] = "pending"
    baseline_metrics: dict[str, str]
    affected_metrics: dict[str, str] = Field(default_factory=dict)
    execution_ids: list[UUID] = Field(default_factory=list)


class Registry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: GenerationConfig
    baseline_metrics: dict[str, str]
    records: list[FaultRecord] = Field(default_factory=list)


CAUSES = {
    "schema_drift": "Producer renamed payload.amount; ingestion contract still expects amount.",
    "delayed_ingestion": "Payment delivery moved beyond the reporting watermark.",
    "duplicate_transactions": "At-least-once source replay without business-key deduplication.",
    "incorrect_join": "Churn join uses region instead of subscription ID.",
    "missing_partitions": "Payment ingestion omitted daily partitions.",
    "currency_conversion": "Daily exchange rate was multiplied by an incorrect factor.",
}


class PrivateRegistry:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "ground_truth.json"

    @contextmanager
    def locked(self) -> Iterator[None]:
        if self.directory.is_symlink():
            raise ValueError("Private directory cannot be a symlink")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.stat().st_mode & 0o077:
            raise PermissionError("Private directory must have mode 0700")
        descriptor = os.open(self.directory / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def read(self) -> Registry:
        if self.path.is_symlink():
            raise ValueError("Registry cannot be a symlink")
        if self.path.stat().st_mode & 0o077:
            raise PermissionError("Ground-truth file must have mode 0600")
        return Registry.model_validate_json(self.path.read_text())

    def write(self, registry: Registry) -> None:
        temporary = self.directory / f"registry-{uuid4()}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(registry.model_dump_json(indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)

    def initialize(self, simulator: Simulator, config: GenerationConfig) -> dict[str, str]:
        with self.locked():
            if self.path.exists():
                raise ValueError(
                    "Fixture exists; use reset or a separate database/private directory"
                )
            with simulator.engine.connect() as connection:
                if connection.scalar(select(func.count()).select_from(customers)):
                    raise ValueError(
                        "Database already contains a fixture; locate its private registry"
                    )
            registry = Registry(config=config, baseline_metrics={})
            self.write(registry)  # Preserve config even if initial loading is interrupted.
            registry.baseline_metrics = simulator.baseline(config, uuid4())
            self.write(registry)
            return registry.baseline_metrics

    def _inject(self, simulator: Simulator, registry: Registry, fault: FaultConfig) -> FaultRecord:
        if not registry.baseline_metrics:
            raise ValueError("Incomplete baseline; run reset first")
        if any(r.status in {"pending", "active"} for r in registry.records):
            raise ValueError("Reset the current fault before injecting or replaying another")
        fault_id = uuid5(UUID(registry.config.fixture_id), fault.digest)
        record = next((r for r in registry.records if r.fault_id == fault_id), None)
        if record is None:
            record = FaultRecord(
                fault_id=fault_id,
                config=fault,
                injected_at=datetime.now(UTC),
                root_cause=CAUSES[fault.kind],
                baseline_metrics=registry.baseline_metrics,
            )
            registry.records.append(record)
        record.status = "pending"
        record.injected_at = datetime.now(UTC)
        record.injection_timestamps.append(record.injected_at)
        execution = uuid4()
        record.execution_ids.append(execution)
        self.write(registry)
        record.affected_metrics = simulator.inject(registry.config, fault, execution)
        record.status = "active"
        self.write(registry)
        return record

    def inject(self, simulator: Simulator, fault: FaultConfig) -> FaultRecord:
        with self.locked():
            return self._inject(simulator, self.read(), fault)

    def _reset(self, simulator: Simulator, registry: Registry) -> dict[str, str]:
        result = simulator.baseline(registry.config, uuid4())
        if registry.baseline_metrics and result != registry.baseline_metrics:
            raise RuntimeError("Reset did not reproduce baseline metrics")
        registry.baseline_metrics = result
        for record in registry.records:
            record.status = "reset"
        self.write(registry)
        return result

    def reset(self, simulator: Simulator) -> dict[str, str]:
        with self.locked():
            return self._reset(simulator, self.read())

    def replay(self, simulator: Simulator, fault_id: UUID) -> FaultRecord:
        with self.locked():
            registry = self.read()
            record = next((r for r in registry.records if r.fault_id == fault_id), None)
            if record is None:
                raise ValueError("Unknown fault ID")
            self._reset(simulator, registry)
            return self._inject(simulator, registry, record.config)

    def scenario_baseline(self, simulator: Simulator, config: GenerationConfig) -> dict[str, str]:
        if not self.path.exists():
            return self.initialize(simulator, config)
        with self.locked():
            registry = self.read()
            if registry.config != config:
                raise ValueError("Scenario config differs from fixture; use a separate fixture DB")
            return self._reset(simulator, registry)

    def dump(self) -> str:
        with self.locked():
            return json.dumps(self.read().model_dump(mode="json"), indent=2)
