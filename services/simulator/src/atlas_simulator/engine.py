"""Evaluator-invoked mutations on a dedicated synthetic database only."""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from atlas_contracts.simulator_events import TelemetryEvent
from atlas_database import schema as s
from sqlalchemy import Connection, Engine, delete, func, insert, select, update

from atlas_simulator.domain import FaultConfig, FaultKind, GenerationConfig
from atlas_simulator.generator import generate, stamp
from atlas_simulator.pipelines import enqueue, run


def load(connection: Connection, config: GenerationConfig) -> None:
    data = generate(config)
    for table in reversed(s.BUSINESS_TABLES):
        connection.execute(delete(table))
    for table in s.BUSINESS_TABLES:
        rows = data.get(table.name, [])
        for offset in range(0, len(rows), 1000):
            connection.execute(insert(table), rows[offset : offset + 1000])


def mutate(connection: Connection, config: GenerationConfig, fault: FaultConfig) -> int:
    end = fault.day + timedelta(days=fault.duration_days)
    if fault.day < config.start or end > config.start + timedelta(days=config.days):
        raise ValueError("Fault window must be contained in the generated fixture")
    # Derived rows are rebuilt after mutation; preserve immutable pipeline run history.
    connection.execute(delete(s.daily_financials))
    connection.execute(delete(s.staged_payments))
    events = s.payment_events
    window = (events.c.partition_day >= fault.day) & (events.c.partition_day < end)
    source_rows = connection.execute(select(events).where(window)).mappings().all()
    if not source_rows:
        raise ValueError("Fault window contains no payment events")
    match fault.kind:
        case FaultKind.MISSING_PARTITIONS:
            connection.execute(delete(events).where(window))
        case FaultKind.DELAYED_INGESTION:
            for row in source_rows:
                connection.execute(
                    update(events)
                    .where(events.c.id == row["id"])
                    .values(arrived_at=row["arrived_at"] + timedelta(days=fault.delay_days))
                )
        case FaultKind.DUPLICATE_TRANSACTIONS:
            for row in source_rows:
                for copy in range(fault.copies):
                    values = dict(row)
                    values["id"] = f"dup-{uuid5(UUID(config.fixture_id), row['id'] + str(copy))}"
                    connection.execute(insert(events).values(**values))
        case FaultKind.SCHEMA_DRIFT:
            for row in source_rows:
                payload = dict(row["payload"])
                payload["gross_amount"] = payload.pop("amount")
                payload["schema_version"] = 3
                connection.execute(
                    update(events).where(events.c.id == row["id"]).values(payload=payload)
                )
            connection.execute(
                insert(s.schema_versions).values(
                    id="payment_events-v3",
                    created_at=stamp(fault.day),
                    dataset_id="payment_events",
                    version=3,
                    effective_at=stamp(fault.day),
                    fields=[
                        "id",
                        "created_at",
                        "payment_id",
                        "event_key",
                        "partition_day",
                        "arrived_at",
                        "payload",
                        "payload.gross_amount",
                        "payload.currency",
                        "payload.processor",
                    ],
                )
            )
            connection.execute(
                insert(s.schema_changes).values(
                    id="payment-v3-change",
                    created_at=stamp(fault.day),
                    dataset_id="payment_events",
                    from_version_id="payment_events-v2",
                    to_version_id="payment_events-v3",
                    description="Renamed payload.amount to payload.gross_amount",
                )
            )
        case FaultKind.INCORRECT_JOIN:
            definition = f"regional_cancellation_join_v2:{fault.day}:{end}"
            connection.execute(
                update(s.pipelines).where(s.pipelines.c.id == "churn").values(definition=definition)
            )
            connection.execute(
                update(s.column_lineage)
                .where(
                    s.column_lineage.c.pipeline_id == "churn",
                    s.column_lineage.c.destination_column == "churned",
                )
                .values(expression="count distinct subscriptions joined by region")
            )
        case FaultKind.CURRENCY_CONVERSION:
            fx = s.currency_rates
            connection.execute(
                update(fx)
                .where(
                    fx.c.currency == fault.currency, fx.c.rate_day >= fault.day, fx.c.rate_day < end
                )
                .values(usd_rate=fx.c.usd_rate * Decimal(str(fault.rate_multiplier)))
            )
    return len(source_rows)


def metrics(connection: Connection) -> dict[str, str]:
    finance = connection.execute(
        select(
            func.coalesce(func.sum(s.daily_financials.c.revenue_usd), 0),
            func.coalesce(func.sum(s.daily_financials.c.payment_count), 0),
        )
    ).one()
    churned = connection.scalar(select(func.coalesce(func.sum(s.churn_metrics.c.churned), 0))) or 0
    return {
        "revenue_usd": str(finance[0]),
        "payment_count": str(finance[1]),
        "churned": str(churned),
    }


def fingerprint(connection: Connection) -> dict[str, list[dict[str, Any]]]:
    """Business/lineage state, excluding execution history and delivery bookkeeping."""
    return {
        t.name: [dict(row) for row in connection.execute(select(t).order_by(t.c.id)).mappings()]
        for t in s.BUSINESS_TABLES
        if t.name != "pipeline_runs"
    }


class Simulator:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def baseline(self, config: GenerationConfig, run_id: UUID) -> dict[str, str]:
        with self.engine.begin() as connection:
            load(connection, config)
            run(connection, config, run_id)
            enqueue(
                connection,
                TelemetryEvent(
                    event_id=uuid5(run_id, "source-telemetry"),
                    run_id=run_id,
                    occurred_at=config.cutoff,
                    component="payment-processor",
                    code="source_connected",
                    severity="info",
                    measurements={"latency_ms": 30},
                ),
            )
            return metrics(connection)

    def inject(self, config: GenerationConfig, fault: FaultConfig, run_id: UUID) -> dict[str, str]:
        with self.engine.begin() as connection:
            affected = mutate(connection, config, fault)
            run(connection, config, run_id)
            event_id = uuid5(run_id, "source-telemetry")
            connection.execute(
                insert(s.operational_events).values(
                    id=str(event_id),
                    created_at=config.cutoff,
                    pipeline_id=None,
                    severity="warning",
                    code="source_or_configuration_changed",
                    details={"affected_rows": affected},
                )
            )
            enqueue(
                connection,
                TelemetryEvent(
                    event_id=event_id,
                    run_id=run_id,
                    occurred_at=config.cutoff,
                    component="analytics-runtime",
                    severity="warning",
                    code="source_or_configuration_changed",
                    measurements={"affected_rows": float(affected)},
                ),
            )
            return metrics(connection)
