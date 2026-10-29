"""Deterministic fictional retailer data; no database, clock, network or LLM access."""

import math
import random
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from atlas_simulator.domain import GenerationConfig

Row = dict[str, Any]
Dataset = dict[str, list[Row]]

REGIONS = [
    ("na", "North America", "USD", "1", 1.3),
    ("eu", "Continental Europe", "EUR", "1.08", 1.0),
    ("uk", "United Kingdom", "GBP", "1.27", 0.9),
    ("jp", "Japan", "JPY", "0.0067", 0.8),
]


def stamp(day: date, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour), UTC)


def generate(config: GenerationConfig) -> Dataset:
    rng = random.Random(config.seed)
    data: Dataset = {}

    def add(table_name: str, identifier: str, **values: Any) -> None:
        data.setdefault(table_name, []).append(
            {"id": identifier, "created_at": stamp(config.start), **values}
        )

    for region, name, currency, _, _ in REGIONS:
        add("regions", region, name=name, currency=currency)
        add("warehouses", f"wh-{region}", region_id=region, name=f"{name} fulfillment")
        add(
            "campaigns",
            f"campaign-{region}",
            region_id=region,
            channel="search" if region in {"na", "uk"} else "social",
            budget=Decimal("12000"),
        )
    for i in range(12):
        product_key = f"product-{i}"
        add(
            "products",
            product_key,
            name=f"Aurora {'Plus' if i % 2 else 'Home'} {i}",
            category="subscription" if i < 4 else "retail",
            price=Decimal(9 + i * 7),
        )
        for region, *_ in REGIONS:
            add(
                "inventory",
                f"stock-{region}-{i}",
                warehouse_id=f"wh-{region}",
                product_id=product_key,
                quantity=config.days * config.orders_per_day * 8,
            )

    stock = {row["id"]: row for row in data["inventory"]}
    cancellations: dict[int, date] = {}
    for i in range(config.customers):
        region = REGIONS[i % 4][0]
        add(
            "customers",
            f"customer-{i}",
            region_id=region,
            email=f"customer{i}@aurora.example",
            segment="business" if i % 5 == 0 else "consumer",
        )
        add("accounts", f"account-{i}", customer_id=f"customer-{i}", status="active")
        # A guaranteed cancellation per region plus seeded heterogeneous churn.
        if i < 4 or rng.random() < (0.12 if region == "na" else 0.2):
            cancellations[i] = config.start + timedelta(days=rng.randrange(3, config.days))
        end = cancellations.get(i)
        add(
            "subscriptions",
            f"subscription-{i}",
            account_id=f"account-{i}",
            product_id=f"product-{i % 4}",
            starts_on=config.start,
            ends_on=end,
            status="cancelled" if end else "active",
        )
        add(
            "customer_events",
            f"signup-{i}",
            account_id=f"account-{i}",
            subscription_id=f"subscription-{i}",
            event_type="signup",
            occurred_on=config.start,
        )
        if end:
            add(
                "customer_events",
                f"cancel-{i}",
                account_id=f"account-{i}",
                subscription_id=f"subscription-{i}",
                event_type="cancellation",
                occurred_on=end,
                created_at=stamp(end),
            )
        if i % 7 == 0:
            add(
                "support_tickets",
                f"ticket-{i}",
                account_id=f"account-{i}",
                order_id=None,
                category="billing" if end else "product",
                status="closed",
                resolved_at=stamp(config.start + timedelta(days=2)),
            )

    def payment(
        identifier: str,
        account: int,
        day: date,
        amount: Decimal,
        order: str | None = None,
        subscription: str | None = None,
    ) -> None:
        currency = REGIONS[account % 4][2]
        occurred = stamp(day, 10 + rng.randrange(8))
        add(
            "payments",
            identifier,
            account_id=f"account-{account}",
            order_id=order,
            subscription_id=subscription,
            currency=currency,
            amount=amount,
            status="captured",
            created_at=occurred,
        )
        delay = rng.randrange(1, 4) if rng.random() < config.late_probability else 0
        version = 1 if (day - config.start).days < config.days // 2 else 2
        payload: Row = {"amount": str(amount), "currency": currency, "schema_version": version}
        if version == 2:
            payload["processor"] = "aurora-pay"
        add(
            "payment_events",
            f"event-{identifier}",
            payment_id=identifier,
            event_key=identifier,
            partition_day=day,
            arrived_at=occurred + timedelta(days=delay),
            payload=payload,
            created_at=occurred,
        )

    for offset in range(config.days):
        day = config.start + timedelta(days=offset)
        weekly = 1.3 if day.weekday() >= 5 else 0.9
        seasonal = 1 + 0.18 * math.cos(2 * math.pi * (day.timetuple().tm_yday - 350) / 365)
        volume = max(4, round(config.orders_per_day * weekly * seasonal * rng.uniform(0.85, 1.15)))
        for _, _, currency, rate, _ in REGIONS:
            add(
                "currency_rates",
                f"fx-{day}-{currency}",
                currency=currency,
                rate_day=day,
                usd_rate=Decimal(rate),
            )
        for j in range(volume):
            region_index = rng.choices(range(4), weights=[x[4] for x in REGIONS])[0]
            account = (
                rng.randrange((config.customers - 1 - region_index) // 4 + 1) * 4 + region_index
            )
            product = rng.choices(range(4, 12), weights=[3, 2, 2, 1, 3, 2, 1, 1])[0]
            quantity = rng.choices([1, 2, 3], weights=[8, 2, 1])[0]
            local_rate = Decimal(REGIONS[region_index][3])
            price = (Decimal(9 + product * 7) / local_rate).quantize(Decimal(".01"))
            total = price * quantity
            stock[f"stock-{REGIONS[region_index][0]}-{product}"]["quantity"] -= quantity
            identifier = f"order-{offset}-{j}"
            add(
                "orders",
                identifier,
                account_id=f"account-{account}",
                ordered_on=day,
                currency=REGIONS[region_index][2],
                total=total,
                created_at=stamp(day),
            )
            add(
                "order_items",
                f"item-{offset}-{j}",
                order_id=identifier,
                product_id=f"product-{product}",
                quantity=quantity,
                unit_price=price,
                created_at=stamp(day),
            )
            payment(f"pay-{identifier}", account, day, total, order=identifier)
            add(
                "marketing_touches",
                f"touch-{offset}-{j}",
                account_id=f"account-{account}",
                campaign_id=f"campaign-{REGIONS[region_index][0]}",
                order_id=identifier,
                attribution_model="last_touch",
                created_at=stamp(day),
            )
        # Monthly renewal attempts produce real subscription revenue and churn-linked cessation.
        for i in range(config.customers):
            if offset % 30 == i % 7 and (i not in cancellations or day < cancellations[i]):
                amount = (Decimal(9 + (i % 4) * 7) / Decimal(REGIONS[i % 4][3])).quantize(
                    Decimal(".01")
                )
                identifier = f"renewal-pay-{offset}-{i}"
                payment(identifier, i, day, amount, subscription=f"subscription-{i}")
                add(
                    "renewals",
                    f"renewal-{offset}-{i}",
                    subscription_id=f"subscription-{i}",
                    payment_id=identifier,
                    renewed_on=day,
                    created_at=stamp(day),
                )
    data["operational_events"] = [
        {
            "id": "incident-latency",
            "created_at": stamp(config.start),
            "pipeline_id": None,
            "severity": "info",
            "code": "source_connected",
            "details": {"source": "payment-processor", "latency_ms": rng.randrange(20, 80)},
        }
    ]
    add_catalog(data, config)
    return data


def add_catalog(data: Dataset, config: GenerationConfig) -> None:
    now = stamp(config.start)
    fields = {
        "payment_events": ["payment_id", "event_key", "partition_day", "arrived_at", "payload"],
        "staged_payments": [
            "event_id",
            "payment_id",
            "business_day",
            "region_id",
            "amount",
            "currency",
        ],
        "daily_financials": ["business_day", "region_id", "payment_count", "revenue_usd"],
        "customer_events": ["account_id", "subscription_id", "event_type", "occurred_on"],
        "subscriptions": ["account_id", "product_id", "starts_on", "ends_on", "status"],
        "churn_metrics": ["business_day", "region_id", "subscribers", "churned", "churn_rate"],
        "currency_rates": ["rate_day", "currency", "usd_rate"],
        "customers": ["region_id", "email", "segment"],
    }
    fields.update(
        {
            "regions": ["name", "currency"],
            "accounts": ["customer_id", "status"],
            "products": ["name", "category", "price"],
            "warehouses": ["region_id", "name"],
            "inventory": ["warehouse_id", "product_id", "quantity"],
            "orders": ["account_id", "ordered_on", "currency", "total"],
            "order_items": ["order_id", "product_id", "quantity", "unit_price"],
            "payments": [
                "account_id",
                "order_id",
                "subscription_id",
                "currency",
                "amount",
                "status",
            ],
            "renewals": ["subscription_id", "payment_id", "renewed_on"],
            "support_tickets": ["account_id", "order_id", "category", "status", "resolved_at"],
            "campaigns": ["region_id", "channel", "budget"],
            "marketing_touches": ["account_id", "campaign_id", "order_id", "attribution_model"],
        }
    )
    fields = {name: ["id", "created_at", *columns] for name, columns in fields.items()}
    data["datasets"] = [
        {"id": name, "created_at": now, "name": name, "owner": "analytics"} for name in fields
    ]
    data["schema_versions"] = [
        {
            "id": f"{name}-v1",
            "created_at": now,
            "dataset_id": name,
            "version": 1,
            "effective_at": now,
            "fields": columns,
        }
        for name, columns in fields.items()
    ]
    data["schema_versions"][0]["fields"] += ["payload.amount", "payload.currency"]
    data["schema_versions"].append(
        {
            "id": "payment_events-v2",
            "created_at": now,
            "dataset_id": "payment_events",
            "version": 2,
            "effective_at": stamp(config.start + timedelta(days=config.days // 2)),
            "fields": [
                *fields["payment_events"],
                "payload.amount",
                "payload.currency",
                "payload.processor",
            ],
        }
    )
    data["schema_changes"] = [
        {
            "id": "payment-v2-change",
            "created_at": now,
            "dataset_id": "payment_events",
            "from_version_id": "payment_events-v1",
            "to_version_id": "payment_events-v2",
            "description": "Added optional processor field",
        }
    ]
    definitions = [
        ("ingest", "payment_events", "staged_payments", "parse_amount_currency_v1"),
        ("finance", "staged_payments", "daily_financials", "sum_amount_times_daily_fx_v1"),
        ("churn", "customer_events", "churn_metrics", "subscription_id_cancellation_join_v1"),
    ]
    data["pipelines"] = [
        {
            "id": name,
            "created_at": now,
            "name": name,
            "source_dataset_id": source,
            "destination_dataset_id": dest,
            "definition": definition,
            "schema_version_id": f"{source}-v2" if source == "payment_events" else f"{source}-v1",
        }
        for name, source, dest, definition in definitions
    ]
    data["pipeline_dependencies"] = [
        {
            "id": "finance-ingest",
            "created_at": now,
            "pipeline_id": "finance",
            "upstream_id": "ingest",
        }
    ]
    lineage = [
        ("ingest", "payment_events", "payload.amount", "staged_payments", "amount", "decimal"),
        ("ingest", "payment_events", "payload.currency", "staged_payments", "currency", "identity"),
        (
            "ingest",
            "payment_events",
            "partition_day",
            "staged_payments",
            "business_day",
            "identity",
        ),
        ("ingest", "payment_events", "payment_id", "staged_payments", "payment_id", "identity"),
        ("ingest", "customers", "region_id", "staged_payments", "region_id", "account lookup"),
        (
            "finance",
            "staged_payments",
            "amount",
            "daily_financials",
            "revenue_usd",
            "sum(amount*fx)",
        ),
        (
            "finance",
            "currency_rates",
            "usd_rate",
            "daily_financials",
            "revenue_usd",
            "sum(amount*fx)",
        ),
        ("finance", "staged_payments", "event_id", "daily_financials", "payment_count", "count"),
        (
            "churn",
            "customer_events",
            "subscription_id",
            "churn_metrics",
            "churned",
            "count distinct",
        ),
        ("churn", "subscriptions", "id", "churn_metrics", "subscribers", "count active"),
    ]
    lineage += [
        ("ingest", "payment_events", "id", "staged_payments", "event_id", "identity"),
        ("ingest", "payments", "account_id", "staged_payments", "region_id", "account lookup"),
        ("ingest", "accounts", "customer_id", "staged_payments", "region_id", "customer lookup"),
        (
            "finance",
            "staged_payments",
            "business_day",
            "daily_financials",
            "business_day",
            "group by",
        ),
        ("finance", "staged_payments", "region_id", "daily_financials", "region_id", "group by"),
        (
            "finance",
            "staged_payments",
            "currency",
            "daily_financials",
            "revenue_usd",
            "currency join",
        ),
        ("finance", "currency_rates", "rate_day", "daily_financials", "revenue_usd", "day join"),
        ("churn", "customer_events", "occurred_on", "churn_metrics", "business_day", "date join"),
        (
            "churn",
            "customer_events",
            "event_type",
            "churn_metrics",
            "churned",
            "cancellation filter",
        ),
        ("churn", "subscriptions", "ends_on", "churn_metrics", "subscribers", "active on day"),
        ("churn", "accounts", "customer_id", "churn_metrics", "region_id", "customer lookup"),
        ("churn", "customers", "region_id", "churn_metrics", "region_id", "group by"),
        ("churn", "subscriptions", "id", "churn_metrics", "churn_rate", "churned / active"),
        (
            "churn",
            "customer_events",
            "subscription_id",
            "churn_metrics",
            "churn_rate",
            "churned / active",
        ),
    ]
    data["column_lineage"] = [
        {
            "id": f"lineage-{i}",
            "created_at": now,
            "pipeline_id": p,
            "source_dataset_id": s,
            "source_column": sc,
            "destination_dataset_id": d,
            "destination_column": dc,
            "expression": expr,
        }
        for i, (p, s, sc, d, dc, expr) in enumerate(lineage)
    ]
