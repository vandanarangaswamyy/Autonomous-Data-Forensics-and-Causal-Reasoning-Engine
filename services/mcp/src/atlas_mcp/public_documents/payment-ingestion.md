# Payment ingestion contract

Payment events carry a payment_id, business event_key, partition_day, arrival timestamp
and JSON payload. The payload contains amount and currency. The processor field is
optional. Ingestion reports exclude events beyond the reporting watermark and record
quality counts. Accounts and customers supply the regional lookup. Schema versions
are recorded in the dataset catalog. Staged payments feed financial reporting.
