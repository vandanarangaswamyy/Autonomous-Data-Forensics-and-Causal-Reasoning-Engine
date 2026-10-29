import json
from pathlib import Path
from uuid import uuid4

import pytest
from atlas_contracts.simulator_events import EVENT_ADAPTER
from atlas_database import schema as s
from atlas_evaluation.registry import PrivateRegistry
from atlas_simulator.domain import FaultConfig, FaultKind, GenerationConfig
from atlas_simulator.engine import Simulator
from atlas_simulator.streaming import consume_once
from pydantic import ValidationError
from sqlalchemy import Connection, Engine, func, select


def test_public_events_and_truth_isolation(db: Engine, tmp_path: Path) -> None:
    config = GenerationConfig()
    simulator = Simulator(db)
    private = PrivateRegistry(tmp_path / "private")
    private.initialize(simulator, config)
    record = private.inject(
        simulator, FaultConfig(kind=FaultKind.SCHEMA_DRIFT, day=config.start, duration_days=2)
    )
    with db.connect() as connection:
        envelopes = list(connection.scalars(select(s.event_outbox.c.envelope)))
        assert {e["event_type"] for e in envelopes} == {
            "pipeline.execution",
            "schema.change",
            "quality.alert",
            "operational.telemetry",
        }
        for envelope in envelopes:
            event = EVENT_ADAPTER.validate_python(envelope)
            assert EVENT_ADAPTER.validate_json(EVENT_ADAPTER.dump_json(event)) == event
            with pytest.raises(ValidationError):
                EVENT_ADAPTER.validate_python({**envelope, "schema_version": 2})
            with pytest.raises(ValidationError):
                EVENT_ADAPTER.validate_python({**envelope, "root_cause": "oracle"})
        public = json.dumps(envelopes)
        assert str(record.fault_id) not in public
        assert record.root_cause not in public
        assert "ground_truth" not in s.metadata.tables
    assert private.directory.stat().st_mode & 0o077 == 0
    assert private.path.stat().st_mode & 0o077 == 0


def test_idempotent_consumer_and_atomic_handler(db: Engine) -> None:
    Simulator(db).baseline(GenerationConfig(), uuid4())
    with db.connect() as connection:
        event = EVENT_ADAPTER.validate_python(connection.scalar(select(s.event_outbox.c.envelope)))
    calls: list[str] = []

    def handler(connection: Connection, event: object) -> None:
        calls.append("applied")

    assert consume_once(db, "test", event, handler)
    assert not consume_once(db, "test", event, handler)
    assert calls == ["applied"]

    def fail(connection: Connection, event: object) -> None:
        raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError):
        consume_once(db, "failing", event, fail)
    with db.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(s.consumer_receipts)
                .where(s.consumer_receipts.c.consumer_group == "failing")
            )
            == 0
        )
    assert consume_once(db, "failing", event, handler)


def test_checked_in_event_schema() -> None:
    assert (
        json.loads(Path("schemas/events/simulator-v1.json").read_text())
        == EVENT_ADAPTER.json_schema()
    )
