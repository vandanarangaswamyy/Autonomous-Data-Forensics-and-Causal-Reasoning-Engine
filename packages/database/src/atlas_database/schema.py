"""Portable simulator schema. Never store evaluator answers in this metadata."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql.schema import SchemaItem

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def fk(name: str, target: str, nullable: bool = False) -> Column[str]:
    return Column(name, String(80), ForeignKey(f"{target}.id"), nullable=nullable, index=True)


def text(name: str, nullable: bool = False) -> Column[str]:
    return Column(name, String(200), nullable=nullable)


def table(name: str, *columns: SchemaItem) -> Table:
    return Table(
        name,
        metadata,
        Column("id", String(80), primary_key=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        *columns,
    )


regions = table("regions", text("name"), text("currency"), UniqueConstraint("name"))
customers = table(
    "customers",
    fk("region_id", "regions"),
    text("email"),
    text("segment"),
    UniqueConstraint("email"),
)
accounts = table(
    "accounts",
    fk("customer_id", "customers"),
    text("status"),
    UniqueConstraint("customer_id"),
    CheckConstraint("status IN ('active','closed')"),
)
products = table(
    "products",
    text("name"),
    text("category"),
    Column("price", Numeric(14, 2), nullable=False),
    CheckConstraint("price > 0"),
)
warehouses = table("warehouses", fk("region_id", "regions"), text("name"))
inventory = table(
    "inventory",
    fk("warehouse_id", "warehouses"),
    fk("product_id", "products"),
    Column("quantity", Integer, nullable=False),
    CheckConstraint("quantity >= 0"),
    UniqueConstraint("warehouse_id", "product_id"),
)
subscriptions = table(
    "subscriptions",
    fk("account_id", "accounts"),
    fk("product_id", "products"),
    Column("starts_on", Date, nullable=False),
    Column("ends_on", Date),
    text("status"),
    CheckConstraint("ends_on IS NULL OR ends_on >= starts_on"),
)
orders = table(
    "orders",
    fk("account_id", "accounts"),
    Column("ordered_on", Date, nullable=False, index=True),
    text("currency"),
    Column("total", Numeric(14, 2), nullable=False),
    CheckConstraint("total >= 0"),
)
order_items = table(
    "order_items",
    fk("order_id", "orders"),
    fk("product_id", "products"),
    Column("quantity", Integer, nullable=False),
    Column("unit_price", Numeric(14, 2), nullable=False),
    CheckConstraint("quantity > 0 AND unit_price > 0"),
)
payments = table(
    "payments",
    fk("account_id", "accounts"),
    fk("order_id", "orders", True),
    fk("subscription_id", "subscriptions", True),
    text("currency"),
    Column("amount", Numeric(14, 2), nullable=False),
    text("status"),
    CheckConstraint("amount > 0"),
    CheckConstraint(
        "(order_id IS NOT NULL AND subscription_id IS NULL) OR "
        "(order_id IS NULL AND subscription_id IS NOT NULL)"
    ),
)
payment_events = table(
    "payment_events",
    fk("payment_id", "payments"),
    text("event_key"),
    Column("partition_day", Date, nullable=False, index=True),
    Column("arrived_at", DateTime(timezone=True), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
)
renewals = table(
    "renewals",
    fk("subscription_id", "subscriptions"),
    fk("payment_id", "payments"),
    Column("renewed_on", Date, nullable=False),
    UniqueConstraint("payment_id"),
)
customer_events = table(
    "customer_events",
    fk("account_id", "accounts"),
    fk("subscription_id", "subscriptions", True),
    text("event_type"),
    Column("occurred_on", Date, nullable=False, index=True),
)
support_tickets = table(
    "support_tickets",
    fk("account_id", "accounts"),
    fk("order_id", "orders", True),
    text("category"),
    text("status"),
    Column("resolved_at", DateTime(timezone=True)),
)
campaigns = table(
    "campaigns",
    fk("region_id", "regions"),
    text("channel"),
    Column("budget", Numeric(14, 2), nullable=False),
    CheckConstraint("budget >= 0"),
)
marketing_touches = table(
    "marketing_touches",
    fk("account_id", "accounts"),
    fk("campaign_id", "campaigns"),
    fk("order_id", "orders", True),
    text("attribution_model"),
)
currency_rates = table(
    "currency_rates",
    text("currency"),
    Column("rate_day", Date, nullable=False),
    Column("usd_rate", Numeric(18, 6), nullable=False),
    CheckConstraint("usd_rate > 0"),
    UniqueConstraint("currency", "rate_day"),
)
staged_payments = table(
    "staged_payments",
    fk("event_id", "payment_events"),
    fk("payment_id", "payments"),
    Column("business_day", Date, nullable=False),
    fk("region_id", "regions"),
    text("currency"),
    Column("amount", Numeric(14, 2), nullable=False),
    CheckConstraint("amount > 0"),
    UniqueConstraint("event_id"),
)
daily_financials = table(
    "daily_financials",
    Column("business_day", Date, nullable=False, index=True),
    fk("region_id", "regions"),
    Column("payment_count", Integer, nullable=False),
    Column("revenue_usd", Numeric(18, 2), nullable=False),
    UniqueConstraint("business_day", "region_id"),
)
churn_metrics = table(
    "churn_metrics",
    Column("business_day", Date, nullable=False, index=True),
    fk("region_id", "regions"),
    Column("subscribers", Integer, nullable=False),
    Column("churned", Integer, nullable=False),
    Column("churn_rate", Numeric(12, 6), nullable=False),
    UniqueConstraint("business_day", "region_id"),
)
datasets = table("datasets", text("name"), text("owner"), UniqueConstraint("name"))
schema_versions = table(
    "schema_versions",
    fk("dataset_id", "datasets"),
    Column("version", Integer, nullable=False),
    Column("effective_at", DateTime(timezone=True), nullable=False),
    Column("fields", JSON, nullable=False),
    UniqueConstraint("dataset_id", "version"),
)
schema_changes = table(
    "schema_changes",
    fk("dataset_id", "datasets"),
    fk("from_version_id", "schema_versions"),
    fk("to_version_id", "schema_versions"),
    Column("description", Text, nullable=False),
)
pipelines = table(
    "pipelines",
    text("name"),
    fk("source_dataset_id", "datasets"),
    fk("destination_dataset_id", "datasets"),
    text("definition"),
    fk("schema_version_id", "schema_versions"),
    UniqueConstraint("name"),
)
pipeline_dependencies = table(
    "pipeline_dependencies",
    fk("pipeline_id", "pipelines"),
    fk("upstream_id", "pipelines"),
    CheckConstraint("pipeline_id <> upstream_id"),
    UniqueConstraint("pipeline_id", "upstream_id"),
)
column_lineage = table(
    "column_lineage",
    fk("pipeline_id", "pipelines"),
    fk("source_dataset_id", "datasets"),
    fk("destination_dataset_id", "datasets"),
    text("source_column"),
    text("destination_column"),
    text("expression"),
)
pipeline_runs = table(
    "pipeline_runs",
    fk("pipeline_id", "pipelines"),
    fk("schema_version_id", "schema_versions"),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=False),
    text("status"),
    Column("input_rows", Integer, nullable=False),
    Column("output_rows", Integer, nullable=False),
    Column("quality", JSON, nullable=False),
    CheckConstraint("finished_at >= started_at"),
    CheckConstraint("status IN ('success','degraded','failed')"),
)
operational_events = table(
    "operational_events",
    fk("pipeline_id", "pipelines", True),
    text("severity"),
    text("code"),
    Column("details", JSON, nullable=False),
)
event_outbox = table(
    "event_outbox",
    Column("envelope", JSON, nullable=False),
    Column("published", Boolean, nullable=False, default=False),
)
consumer_receipts = table(
    "consumer_receipts",
    text("consumer_group"),
    text("event_id"),
    Column("envelope", JSON, nullable=False),
    UniqueConstraint("consumer_group", "event_id"),
)
Index("ix_payment_events_key_day", payment_events.c.event_key, payment_events.c.partition_day)
BUSINESS_TABLES = [
    t for t in metadata.sorted_tables if t.name not in {"event_outbox", "consumer_receipts"}
]
