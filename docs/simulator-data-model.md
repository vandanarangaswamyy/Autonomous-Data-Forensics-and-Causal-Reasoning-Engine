# Simulator data model

Thirty application tables, plus the Alembic revision bookkeeping table.

| Table | Columns | Foreign keys |
|---|---|---|
| consumer_receipts | `id`, `created_at`, `consumer_group`, `event_id`, `envelope` | — |
| currency_rates | `id`, `created_at`, `currency`, `rate_day`, `usd_rate` | — |
| datasets | `id`, `created_at`, `name`, `owner` | — |
| event_outbox | `id`, `created_at`, `envelope`, `published` | — |
| products | `id`, `created_at`, `name`, `category`, `price` | — |
| regions | `id`, `created_at`, `name`, `currency` | — |
| campaigns | `id`, `created_at`, `region_id`, `channel`, `budget` | `region_id` → `regions.id` |
| churn_metrics | `id`, `created_at`, `business_day`, `region_id`, `subscribers`, `churned`, `churn_rate` | `region_id` → `regions.id` |
| customers | `id`, `created_at`, `region_id`, `email`, `segment` | `region_id` → `regions.id` |
| daily_financials | `id`, `created_at`, `business_day`, `region_id`, `payment_count`, `revenue_usd` | `region_id` → `regions.id` |
| schema_versions | `id`, `created_at`, `dataset_id`, `version`, `effective_at`, `fields` | `dataset_id` → `datasets.id` |
| warehouses | `id`, `created_at`, `region_id`, `name` | `region_id` → `regions.id` |
| accounts | `id`, `created_at`, `customer_id`, `status` | `customer_id` → `customers.id` |
| inventory | `id`, `created_at`, `warehouse_id`, `product_id`, `quantity` | `product_id` → `products.id`, `warehouse_id` → `warehouses.id` |
| pipelines | `id`, `created_at`, `name`, `source_dataset_id`, `destination_dataset_id`, `definition`, `schema_version_id` | `destination_dataset_id` → `datasets.id`, `schema_version_id` → `schema_versions.id`, `source_dataset_id` → `datasets.id` |
| schema_changes | `id`, `created_at`, `dataset_id`, `from_version_id`, `to_version_id`, `description` | `dataset_id` → `datasets.id`, `from_version_id` → `schema_versions.id`, `to_version_id` → `schema_versions.id` |
| column_lineage | `id`, `created_at`, `pipeline_id`, `source_dataset_id`, `destination_dataset_id`, `source_column`, `destination_column`, `expression` | `destination_dataset_id` → `datasets.id`, `pipeline_id` → `pipelines.id`, `source_dataset_id` → `datasets.id` |
| operational_events | `id`, `created_at`, `pipeline_id`, `severity`, `code`, `details` | `pipeline_id` → `pipelines.id` |
| orders | `id`, `created_at`, `account_id`, `ordered_on`, `currency`, `total` | `account_id` → `accounts.id` |
| pipeline_dependencies | `id`, `created_at`, `pipeline_id`, `upstream_id` | `pipeline_id` → `pipelines.id`, `upstream_id` → `pipelines.id` |
| pipeline_runs | `id`, `created_at`, `pipeline_id`, `schema_version_id`, `started_at`, `finished_at`, `status`, `input_rows`, `output_rows`, `quality` | `pipeline_id` → `pipelines.id`, `schema_version_id` → `schema_versions.id` |
| subscriptions | `id`, `created_at`, `account_id`, `product_id`, `starts_on`, `ends_on`, `status` | `account_id` → `accounts.id`, `product_id` → `products.id` |
| customer_events | `id`, `created_at`, `account_id`, `subscription_id`, `event_type`, `occurred_on` | `account_id` → `accounts.id`, `subscription_id` → `subscriptions.id` |
| marketing_touches | `id`, `created_at`, `account_id`, `campaign_id`, `order_id`, `attribution_model` | `account_id` → `accounts.id`, `campaign_id` → `campaigns.id`, `order_id` → `orders.id` |
| order_items | `id`, `created_at`, `order_id`, `product_id`, `quantity`, `unit_price` | `order_id` → `orders.id`, `product_id` → `products.id` |
| payments | `id`, `created_at`, `account_id`, `order_id`, `subscription_id`, `currency`, `amount`, `status` | `account_id` → `accounts.id`, `order_id` → `orders.id`, `subscription_id` → `subscriptions.id` |
| support_tickets | `id`, `created_at`, `account_id`, `order_id`, `category`, `status`, `resolved_at` | `account_id` → `accounts.id`, `order_id` → `orders.id` |
| payment_events | `id`, `created_at`, `payment_id`, `event_key`, `partition_day`, `arrived_at`, `payload` | `payment_id` → `payments.id` |
| renewals | `id`, `created_at`, `subscription_id`, `payment_id`, `renewed_on` | `payment_id` → `payments.id`, `subscription_id` → `subscriptions.id` |
| staged_payments | `id`, `created_at`, `event_id`, `payment_id`, `business_day`, `region_id`, `currency`, `amount` | `event_id` → `payment_events.id`, `payment_id` → `payments.id`, `region_id` → `regions.id` |

All tables have primary keys and creation timestamps. Foreign-key columns are indexed.
Business uniqueness, positive amounts/quantities, date ordering and execution status
constraints are declared in SQLAlchemy metadata and explicit Alembic migration 0001.
Public lineage and schema histories describe evidence; no ground-truth table exists.
