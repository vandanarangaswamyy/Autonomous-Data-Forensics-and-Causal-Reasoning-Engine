import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from atlas_database import schema as s
from atlas_database.connection import engine_for
from atlas_evaluation.registry import PrivateRegistry
from atlas_simulator.domain import FaultConfig, FaultKind, GenerationConfig
from atlas_simulator.engine import Simulator, fingerprint
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("kind", list(FaultKind))
def test_real_postgres_faults(postgres_db: Engine, tmp_path: Path, kind: FaultKind) -> None:
    config = GenerationConfig()
    simulator = Simulator(postgres_db)
    registry = PrivateRegistry(tmp_path / "oracle")
    baseline = registry.initialize(simulator, config)
    with postgres_db.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), s.metadata) == []
        before = fingerprint(connection)
    fault = FaultConfig(kind=kind, day=config.start + timedelta(days=17), duration_days=3)
    if kind == FaultKind.INCORRECT_JOIN:
        fault = FaultConfig(kind=kind, day=config.start, duration_days=28)
    result = registry.inject(simulator, fault)
    metric = "churned" if kind == FaultKind.INCORRECT_JOIN else "revenue_usd"
    assert Decimal(result.affected_metrics[metric]) != Decimal(baseline[metric])
    registry.reset(simulator)
    with postgres_db.connect() as connection:
        assert fingerprint(connection) == before
    assert registry.replay(simulator, result.fault_id).affected_metrics == result.affected_metrics


def test_observer_privileges() -> None:
    url = os.environ.get("ATLAS_TEST_READER_URL")
    if not url:
        pytest.skip("Set ATLAS_TEST_READER_URL after bootstrap --grant-reader")
    engine = engine_for(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(s.customers)) is not None
        for forbidden in [
            "DELETE FROM customers",
            "SELECT * FROM event_outbox",
            "SELECT pg_read_file('/etc/passwd')",
            "CREATE TABLE oracle (id int)",
        ]:
            with engine.begin() as connection, pytest.raises(DBAPIError):
                connection.execute(text(forbidden))
    finally:
        engine.dispose()
