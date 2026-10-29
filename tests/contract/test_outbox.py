from uuid import uuid4

import pytest
from atlas_contracts.simulator_events import SimulatorEvent
from atlas_database import schema as s
from atlas_simulator.domain import GenerationConfig
from atlas_simulator.engine import Simulator
from atlas_simulator.streaming import publish_outbox
from sqlalchemy import Engine, func, select


class FailedPublisher:
    def publish(self, event: SimulatorEvent) -> None:
        raise RuntimeError("broker unavailable")


class CapturingPublisher:
    def __init__(self) -> None:
        self.ids: list[str] = []

    def publish(self, event: SimulatorEvent) -> None:
        self.ids.append(str(event.event_id))


def test_delivery_failure_and_replay(db: Engine) -> None:
    Simulator(db).baseline(GenerationConfig(), uuid4())
    with pytest.raises(RuntimeError, match="broker unavailable"):
        publish_outbox(db, FailedPublisher())
    with db.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(s.event_outbox)
                .where(s.event_outbox.c.published.is_(True))
            )
            == 0
        )
    publisher = CapturingPublisher()
    count = publish_outbox(db, publisher, batch_size=3)
    original = list(publisher.ids)
    assert count == len(set(original))
    assert publish_outbox(db, publisher) == 0
    assert publish_outbox(db, publisher, replay=True, batch_size=3) == count
    assert publisher.ids == original + original
