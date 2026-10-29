from datetime import timedelta
from decimal import Decimal
from graphlib import CycleError
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from atlas_database import schema as s
from atlas_evaluation.registry import PrivateRegistry
from atlas_simulator.domain import FaultConfig, FaultKind, GenerationConfig, Profile, Scenario
from atlas_simulator.engine import Simulator, fingerprint
from atlas_simulator.generator import generate
from atlas_simulator.pipelines import dependencies, run
from sqlalchemy import Engine, func, insert, inspect, select, update
from sqlalchemy.exc import IntegrityError


def test_deterministic_generation() -> None:
    config = GenerationConfig()
    first = generate(config)
    assert first == generate(config)
    assert first != generate(config.model_copy(update={"seed": 43}))
    assert len(first["customers"]) == config.customers
    assert len(first["currency_rates"]) == config.days * 4
    assert any(row["arrived_at"] > row["created_at"] for row in first["payment_events"])
    assert {row["payload"]["schema_version"] for row in first["payment_events"]} == {1, 2}
    assert {row["region_id"] for row in first["customers"]} == {"na", "eu", "uk", "jp"}
    assert len(first["orders"]) > 500
    sizes = [GenerationConfig.profile(profile) for profile in Profile]
    assert sizes[0].customers < sizes[1].customers < sizes[2].customers
    assert sizes[0].days < sizes[1].days < sizes[2].days


def test_schema_integrity_and_actual_transformations(db: Engine) -> None:
    config = GenerationConfig()
    Simulator(db).baseline(config, uuid4())
    assert len(s.metadata.tables) == 30
    with db.begin() as connection:
        assert set(inspect(connection).get_table_names()) == set(s.metadata.tables) | {
            "alembic_version"
        }
        assert compare_metadata(MigrationContext.configure(connection), s.metadata) == []
        # Validate every FK, not only a sample relationship.
        for table in s.metadata.tables.values():
            for foreign_key in table.foreign_keys:
                target = foreign_key.column
                missing = connection.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(foreign_key.parent.is_not(None), ~foreign_key.parent.in_(select(target)))
                )
                assert missing == 0, str(foreign_key)
        total = connection.scalar(select(func.sum(s.daily_financials.c.payment_count)))
        staged = connection.scalar(select(func.count()).select_from(s.staged_payments))
        assert total == staged
        assert connection.scalar(select(func.sum(s.churn_metrics.c.churned))) == connection.scalar(
            select(func.count())
            .select_from(s.customer_events)
            .where(s.customer_events.c.event_type == "cancellation")
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(update(s.orders).values(account_id="nonexistent"))


def test_pipeline_dependencies_and_cycles(db: Engine) -> None:
    config = GenerationConfig()
    Simulator(db).baseline(config, uuid4())
    with db.begin() as connection:
        assert dependencies(connection) == {"ingest": set(), "finance": {"ingest"}, "churn": set()}
        runs = connection.execute(
            select(s.pipeline_runs).order_by(s.pipeline_runs.c.started_at)
        ).all()
        ingest = next(row for row in runs if row.pipeline_id == "ingest")
        finance = next(row for row in runs if row.pipeline_id == "finance")
        assert finance.started_at >= ingest.finished_at
        connection.execute(
            insert(s.pipeline_dependencies).values(
                id="cycle", created_at=config.cutoff, pipeline_id="ingest", upstream_id="finance"
            )
        )
        with pytest.raises(CycleError):
            run(connection, config, uuid4())


@pytest.mark.parametrize("kind", list(FaultKind))
def test_fault_impact_reset_and_replay(db: Engine, tmp_path: Path, kind: FaultKind) -> None:
    config = GenerationConfig()
    simulator = Simulator(db)
    registry = PrivateRegistry(tmp_path / "oracle")
    baseline = registry.initialize(simulator, config)
    with db.connect() as connection:
        original = fingerprint(connection)
    fault = FaultConfig(kind=kind, day=config.start + timedelta(days=17), duration_days=3)
    if kind == FaultKind.INCORRECT_JOIN:
        fault = FaultConfig(kind=kind, day=config.start, duration_days=28)
    record = registry.inject(simulator, fault)
    with db.connect() as connection:
        quality_rows = list(connection.scalars(select(s.pipeline_runs.c.quality)))
        checks = {
            key: max(float(row.get(key, 0)) for row in quality_rows)
            for key in {key for row in quality_rows for key in row}
        }
        expected_checks = {
            FaultKind.SCHEMA_DRIFT: "invalid_rows",
            FaultKind.DELAYED_INGESTION: "late_rows",
            FaultKind.DUPLICATE_TRANSACTIONS: "duplicate_rows",
            FaultKind.MISSING_PARTITIONS: "missing_partition_days",
            FaultKind.CURRENCY_CONVERSION: "fx_rate_jump_rows",
        }
        if kind in expected_checks:
            assert checks[expected_checks[kind]] > 0
    assert record.affected_metrics != baseline
    if kind == FaultKind.INCORRECT_JOIN:
        assert Decimal(record.affected_metrics["churned"]) > Decimal(baseline["churned"])
    elif kind == FaultKind.DUPLICATE_TRANSACTIONS:
        assert Decimal(record.affected_metrics["payment_count"]) > Decimal(
            baseline["payment_count"]
        )
    else:
        assert Decimal(record.affected_metrics["revenue_usd"]) < Decimal(baseline["revenue_usd"])
    with db.connect() as connection:
        changed = fingerprint(connection)
        assert changed != original
    with pytest.raises(ValueError, match="Reset"):
        registry.inject(simulator, fault)
    assert registry.reset(simulator) == baseline
    with db.connect() as connection:
        assert fingerprint(connection) == original
    replayed = registry.replay(simulator, record.fault_id)
    assert replayed.fault_id == record.fault_id
    assert replayed.affected_metrics == record.affected_metrics
    assert len(replayed.execution_ids) == 2
    assert len(replayed.injection_timestamps) == 2
    assert registry.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("path", sorted(Path("evaluation/scenarios").glob("*.json")))
def test_end_to_end_benchmarks(db: Engine, tmp_path: Path, path: Path) -> None:
    scenario = Scenario.model_validate_json(path.read_text())
    registry = PrivateRegistry(tmp_path / "oracle")
    simulator = Simulator(db)
    baseline = registry.initialize(simulator, scenario.config)
    with db.connect() as connection:
        baseline_data = fingerprint(connection)
    record = registry.inject(simulator, scenario.fault)
    with db.connect() as connection:
        fault_data = fingerprint(connection)
    for dataset in scenario.affected_datasets:
        assert fault_data[dataset] != baseline_data[dataset]
    assert fault_data["payments"] == baseline_data["payments"]
    before = Decimal(baseline[scenario.metric])
    after = Decimal(record.affected_metrics[scenario.metric])
    assert after > before if scenario.direction == "increase" else after < before
    assert scenario.affected_datasets and scenario.expected_evidence
    assert registry.reset(simulator) == baseline


def test_migration_downgrade_and_upgrade(db: Engine) -> None:
    config = Config("alembic.ini")
    with db.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        assert inspect(connection).get_table_names() == ["alembic_version"]
        command.upgrade(config, "head")
        assert compare_metadata(MigrationContext.configure(connection), s.metadata) == []


def test_pending_injection_recovery(
    db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = GenerationConfig()
    registry = PrivateRegistry(tmp_path / "private")
    simulator = Simulator(db)
    baseline = registry.initialize(simulator, config)

    def interrupted(
        self: Simulator, config: GenerationConfig, fault: FaultConfig, run_id: object
    ) -> dict[str, str]:
        raise RuntimeError("simulated operator interruption")

    monkeypatch.setattr(Simulator, "inject", interrupted)
    with pytest.raises(RuntimeError):
        registry.inject(simulator, FaultConfig(kind=FaultKind.MISSING_PARTITIONS, day=config.start))
    assert registry.read().records[0].status == "pending"
    with pytest.raises(ValueError, match="Reset"):
        registry.inject(simulator, FaultConfig(kind=FaultKind.MISSING_PARTITIONS, day=config.start))
    assert registry.reset(simulator) == baseline
    assert registry.read().records[0].status == "reset"


def test_private_store_rejects_public_permissions(tmp_path: Path) -> None:
    directory = tmp_path / "oracle"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    with pytest.raises(PermissionError), PrivateRegistry(directory).locked():
        pytest.fail("Public oracle directory was accepted")
