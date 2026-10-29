"""Executable SQL transformations. This is fixture code, not a diagnostic SQL tool."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from graphlib import TopologicalSorter
from typing import Any
from uuid import UUID, uuid5

from atlas_contracts.simulator_events import (
    PipelineEvent,
    QualityEvent,
    SchemaEvent,
    SimulatorEvent,
)
from atlas_database import schema as s
from sqlalchemy import Connection, and_, delete, func, insert, select

from atlas_simulator.domain import GenerationConfig
from atlas_simulator.generator import stamp


def enqueue(connection: Connection, event: SimulatorEvent) -> None:
    connection.execute(
        insert(s.event_outbox).values(
            id=str(event.event_id),
            created_at=event.occurred_at,
            envelope=event.model_dump(mode="json"),
            published=False,
        )
    )


def dependencies(connection: Connection) -> dict[str, set[str]]:
    graph = {str(value): set[str]() for value in connection.scalars(select(s.pipelines.c.id))}
    for row in connection.execute(select(s.pipeline_dependencies)):
        graph[row.pipeline_id].add(row.upstream_id)
    return graph


def ingest(connection: Connection, config: GenerationConfig) -> tuple[int, int, dict[str, float]]:
    connection.execute(delete(s.staged_payments))
    source = s.payment_events.join(s.payments).join(s.accounts).join(s.customers)
    query = select(s.payment_events, s.customers.c.region_id).select_from(source)
    rows: list[dict[str, Any]] = []
    bad, late = 0, 0
    seen: set[str] = set()
    duplicates = 0
    total = 0
    for row in connection.execute(query).mappings():
        total += 1
        arrived = row["arrived_at"]
        if arrived.tzinfo is None:
            from datetime import UTC

            arrived = arrived.replace(tzinfo=UTC)
        if arrived > config.cutoff:
            late += 1
            continue
        try:
            amount = Decimal(row["payload"]["amount"])
            currency = row["payload"]["currency"]
            if (
                not amount.is_finite()
                or amount <= 0
                or currency not in {"USD", "EUR", "GBP", "JPY"}
            ):
                raise ValueError("Unsupported payment")
        except (KeyError, ValueError, InvalidOperation, TypeError):
            bad += 1
            continue
        duplicates += int(row["event_key"] in seen)
        seen.add(row["event_key"])
        rows.append(
            {
                "id": f"staged-{row['id']}",
                "created_at": config.cutoff,
                "event_id": row["id"],
                "payment_id": row["payment_id"],
                "business_day": row["partition_day"],
                "region_id": row["region_id"],
                "currency": currency,
                "amount": amount,
            }
        )
        if len(rows) >= 1000:
            connection.execute(insert(s.staged_payments), rows)
            rows.clear()
    if rows:
        connection.execute(insert(s.staged_payments), rows)
    existing_days = set(connection.scalars(select(s.payment_events.c.partition_day).distinct()))
    missing = sum(config.start + timedelta(days=i) not in existing_days for i in range(config.days))
    output = total - bad - late
    return (
        total,
        output,
        {
            "invalid_rows": float(bad),
            "late_rows": float(late),
            "duplicate_rows": float(duplicates),
            "missing_partition_days": float(missing),
        },
    )


def finance(connection: Connection, config: GenerationConfig) -> tuple[int, int, dict[str, float]]:
    connection.execute(delete(s.daily_financials))
    st, fx = s.staged_payments, s.currency_rates
    source = st.join(fx, and_(st.c.currency == fx.c.currency, st.c.business_day == fx.c.rate_day))
    query = select(
        st.c.business_day,
        st.c.region_id,
        func.count().label("payment_count"),
        func.sum(st.c.amount * fx.c.usd_rate).label("revenue_usd"),
    ).select_from(source)
    query = query.group_by(st.c.business_day, st.c.region_id)
    rows = [
        {
            "id": f"finance-{row.business_day}-{row.region_id}",
            "created_at": config.cutoff,
            "business_day": row.business_day,
            "region_id": row.region_id,
            "payment_count": row.payment_count,
            "revenue_usd": Decimal(row.revenue_usd).quantize(Decimal(".01")),
        }
        for row in connection.execute(query)
    ]
    if rows:
        connection.execute(insert(s.daily_financials), rows)
    input_rows = connection.scalar(select(func.count()).select_from(st)) or 0
    matched = sum(int(row["payment_count"]) for row in rows)
    previous: dict[str, Decimal] = {}
    jumps = 0
    for rate in connection.execute(select(fx).order_by(fx.c.currency, fx.c.rate_day)):
        prior = previous.get(rate.currency)
        if prior and abs(rate.usd_rate / prior - 1) > Decimal(".25"):
            jumps += 1
        previous[rate.currency] = rate.usd_rate
    return (
        input_rows,
        len(rows),
        {"unmatched_currency_rows": float(input_rows - matched), "fx_rate_jump_rows": float(jumps)},
    )


def churn(connection: Connection, config: GenerationConfig) -> tuple[int, int, dict[str, float]]:
    connection.execute(delete(s.churn_metrics))
    sub, events = s.subscriptions, s.customer_events
    owner = s.customers.alias("owner")
    cancelling = s.customers.alias("cancelling_customer")
    event_account = s.accounts.alias("event_account")
    source = sub.join(s.accounts).join(owner)
    definition = connection.scalar(
        select(s.pipelines.c.definition).where(s.pipelines.c.id == "churn")
    )
    normal_source = source.join(events, events.c.subscription_id == sub.c.id)

    def cancellation_query(joined: Any) -> Any:
        return (
            select(
                events.c.occurred_on,
                owner.c.region_id,
                func.count(func.distinct(sub.c.id)).label("churned"),
            )
            .select_from(joined)
            .where(events.c.event_type == "cancellation")
            .group_by(events.c.occurred_on, owner.c.region_id)
        )

    query = cancellation_query(normal_source)
    counts: dict[tuple[date, str], int] = {}
    if str(definition).startswith("regional_cancellation_join_v2:"):
        _, start_text, end_text = str(definition).split(":")
        start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
        window = (events.c.occurred_on >= start) & (events.c.occurred_on < end)
        incorrect_source = (
            source.join(cancelling, owner.c.region_id == cancelling.c.region_id)
            .join(event_account, event_account.c.customer_id == cancelling.c.id)
            .join(events, events.c.account_id == event_account.c.id)
        )
        for row in connection.execute(cancellation_query(incorrect_source).where(window)):
            counts[(row.occurred_on, row.region_id)] = row.churned
        query = query.where(~window)
    for row in connection.execute(query):
        counts[(row.occurred_on, row.region_id)] = row.churned
    subscriber_rows = connection.execute(
        select(sub.c.starts_on, sub.c.ends_on, owner.c.region_id).select_from(
            sub.join(s.accounts).join(owner)
        )
    ).all()
    rows: list[dict[str, Any]] = []
    for offset in range(config.days):
        day = config.start + timedelta(days=offset)
        denominators: dict[str, int] = defaultdict(int)
        for subscription in subscriber_rows:
            if subscription.starts_on <= day and (
                subscription.ends_on is None or subscription.ends_on >= day
            ):
                denominators[subscription.region_id] += 1
        for region in connection.scalars(select(s.regions.c.id)):
            denominator = denominators[region]
            cancelled = counts.get((day, region), 0)
            rows.append(
                {
                    "id": f"churn-{day}-{region}",
                    "created_at": config.cutoff,
                    "business_day": day,
                    "region_id": region,
                    "subscribers": denominator,
                    "churned": cancelled,
                    "churn_rate": Decimal(cancelled) / max(1, denominator),
                }
            )
    connection.execute(insert(s.churn_metrics), rows)
    total = connection.scalar(select(func.count()).select_from(events)) or 0
    return (
        total,
        len(rows),
        {"excess_churn_rows": float(sum(max(0, r["churned"] - r["subscribers"]) for r in rows))},
    )


def run(connection: Connection, config: GenerationConfig, run_id: UUID) -> None:
    graph = dependencies(connection)
    order = list(TopologicalSorter(graph).static_order())  # raises on cycles before mutation
    # Recompute downstream before removing upstream rows referenced by it.
    connection.execute(delete(s.daily_financials))
    transformations = {"ingest": ingest, "finance": finance, "churn": churn}
    for position, pipeline_id in enumerate(order):
        started = config.cutoff + timedelta(seconds=position * 2)
        finished = started + timedelta(seconds=1)
        input_rows, output_rows, quality = transformations[pipeline_id](connection, config)
        status = "degraded" if any(quality.values()) else "success"
        execution_id = uuid5(run_id, pipeline_id)
        version = connection.scalar(
            select(s.pipelines.c.schema_version_id).where(s.pipelines.c.id == pipeline_id)
        )
        connection.execute(
            insert(s.pipeline_runs).values(
                id=str(execution_id),
                created_at=started,
                pipeline_id=pipeline_id,
                schema_version_id=version,
                started_at=started,
                finished_at=finished,
                status=status,
                input_rows=input_rows,
                output_rows=output_rows,
                quality=quality,
            )
        )
        enqueue(
            connection,
            PipelineEvent(
                event_id=execution_id,
                run_id=run_id,
                occurred_at=finished,
                pipeline_id=pipeline_id,
                status=status,
                input_rows=input_rows,
                output_rows=output_rows,
                quality=quality,
            ),
        )
        destination = connection.scalar(
            select(s.pipelines.c.destination_dataset_id).where(s.pipelines.c.id == pipeline_id)
        )
        for check, observed in quality.items():
            if observed:
                enqueue(
                    connection,
                    QualityEvent(
                        event_id=uuid5(run_id, f"{pipeline_id}:{check}"),
                        run_id=run_id,
                        occurred_at=finished,
                        dataset=str(destination),
                        check=check,
                        observed=observed,
                        threshold=0,
                    ),
                )
    for version_row in connection.execute(select(s.schema_versions)):
        enqueue(
            connection,
            SchemaEvent(
                event_id=uuid5(run_id, version_row.id),
                run_id=run_id,
                occurred_at=stamp(version_row.effective_at.date()),
                dataset=version_row.dataset_id,
                version=version_row.version,
                fields=tuple(version_row.fields),
            ),
        )
