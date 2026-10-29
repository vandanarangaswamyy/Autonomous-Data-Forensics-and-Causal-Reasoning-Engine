"""At-least-once transactional outbox and durable idempotent consumer inbox."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from atlas_contracts.simulator_events import EVENT_ADAPTER, SimulatorEvent
from atlas_database import schema as s
from confluent_kafka import Consumer, KafkaError, Message, Producer
from sqlalchemy import Connection, Engine, insert, select, update
from sqlalchemy.exc import IntegrityError

EventHandler = Callable[[Connection, SimulatorEvent], None]


def consume_once(
    engine: Engine, group: str, event: SimulatorEvent, handler: EventHandler | None = None
) -> bool:
    """Receipt and handler effects share a transaction; duplicates do not invoke handler."""
    with engine.begin() as connection:
        try:
            with connection.begin_nested():
                connection.execute(
                    insert(s.consumer_receipts).values(
                        id=str(uuid5(NAMESPACE_URL, f"{group}:{event.event_id}")),
                        created_at=datetime.now(UTC),
                        consumer_group=group,
                        event_id=str(event.event_id),
                        envelope=event.model_dump(mode="json"),
                    )
                )
        except IntegrityError:
            existing = connection.scalar(
                select(s.consumer_receipts.c.id).where(
                    s.consumer_receipts.c.consumer_group == group,
                    s.consumer_receipts.c.event_id == str(event.event_id),
                )
            )
            if existing:
                return False
            raise
        if handler:
            handler(connection, event)
        return True


class EventPublisher(Protocol):
    def publish(self, event: SimulatorEvent) -> None: ...


class KafkaPublisher:
    def __init__(self, bootstrap: str, topic: str) -> None:
        self.topic = topic
        self.producer = Producer(
            {
                "bootstrap.servers": bootstrap,
                "enable.idempotence": True,
                "acks": "all",
                "message.timeout.ms": 10000,
            }
        )

    def publish(self, event: SimulatorEvent) -> None:
        errors: list[str] = []

        def delivered(error: KafkaError | None, message: Message) -> None:
            if error:
                errors.append(str(error))

        self.producer.produce(
            self.topic,
            key=str(event.run_id),
            value=EVENT_ADAPTER.dump_json(event),
            on_delivery=delivered,
        )
        remaining = self.producer.flush(15)
        if remaining or errors:
            raise RuntimeError(f"Kafka delivery failed ({remaining} pending): {errors}")


def publish_outbox(
    engine: Engine, publisher: EventPublisher, replay: bool = False, batch_size: int = 100
) -> int:
    published = 0
    last_id = ""
    while True:
        with engine.connect() as connection:
            query = (
                select(s.event_outbox)
                .where(s.event_outbox.c.id > last_id)
                .order_by(s.event_outbox.c.id)
                .limit(batch_size)
            )
            if not replay:
                query = query.where(s.event_outbox.c.published.is_(False))
            rows = connection.execute(query).mappings().all()
        if not rows:
            break
        for row in rows:
            publisher.publish(EVENT_ADAPTER.validate_python(row["envelope"]))
            with engine.begin() as connection:
                connection.execute(
                    update(s.event_outbox)
                    .where(s.event_outbox.c.id == row["id"])
                    .values(published=True)
                )
            last_id = row["id"]
            published += 1
    return published


def consume_stream(
    engine: Engine,
    bootstrap: str,
    topic: str,
    group: str,
    max_messages: int,
    idle_seconds: float = 10,
) -> tuple[int, int]:
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
        }
    )
    received, applied = 0, 0
    try:
        consumer.subscribe([topic])
        while received < max_messages:
            message = consumer.poll(idle_seconds)
            if message is None:
                break
            if message.error():
                raise RuntimeError(str(message.error()))
            value = message.value()
            if not isinstance(value, bytes):
                raise ValueError("Expected a non-tombstone JSON event")
            event = EVENT_ADAPTER.validate_json(value)
            applied += int(consume_once(engine, group, event))
            received += 1
            # Offset only advances after durable receipt/effects commit.
            consumer.commit(message=message, asynchronous=False)
    finally:
        consumer.close()
    return received, applied
