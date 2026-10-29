import os
from pathlib import Path
from uuid import uuid4

import pytest
from atlas_database import schema as s
from atlas_simulator.domain import GenerationConfig
from atlas_simulator.engine import Simulator
from atlas_simulator.streaming import KafkaPublisher, consume_stream, publish_outbox
from confluent_kafka.admin import AdminClient, NewTopic  # type: ignore[attr-defined]
from sqlalchemy import Engine, func, select

pytestmark = pytest.mark.integration


def test_real_kafka_replay_and_durable_dedup(postgres_db: Engine, tmp_path: Path) -> None:
    bootstrap = os.environ.get("ATLAS_TEST_KAFKA_BOOTSTRAP")
    if not bootstrap:
        pytest.skip("Set ATLAS_TEST_KAFKA_BOOTSTRAP to run real Kafka integration")
    topic = f"atlas-test-{uuid4().hex}"
    admin = AdminClient({"bootstrap.servers": bootstrap})
    for future in admin.create_topics(
        [NewTopic(topic, num_partitions=1, replication_factor=1)]
    ).values():
        future.result(timeout=15)
    try:
        Simulator(postgres_db).baseline(GenerationConfig(), uuid4())
        publisher = KafkaPublisher(bootstrap, topic)
        sent = publish_outbox(postgres_db, publisher)
        assert sent > 3
        assert publish_outbox(postgres_db, publisher) == 0
        group = f"observer-{uuid4()}"
        assert consume_stream(postgres_db, bootstrap, topic, group, sent, 15) == (sent, sent)
        assert publish_outbox(postgres_db, publisher, replay=True) == sent
        # New consumer instance resumes after its durable Kafka offset, then deduplicates replay.
        assert consume_stream(postgres_db, bootstrap, topic, group, sent, 15) == (sent, 0)
        with postgres_db.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(s.consumer_receipts)) == sent
            assert (
                connection.scalar(
                    select(func.count())
                    .select_from(s.event_outbox)
                    .where(s.event_outbox.c.published.is_(False))
                )
                == 0
            )
    finally:
        for future in admin.delete_topics([topic]).values():
            future.result(timeout=15)
