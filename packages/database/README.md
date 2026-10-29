# Database infrastructure

Portable SQLAlchemy 2.0 simulator tables and connection factory are in src/atlas_database.
Alembic owns migrations; run `make sim-migrate` from the repository root after bootstrap.
The simulator uses a dedicated database. No investigation persistence, diagnostic SQL
adapter, Teradata adapter or tool gateway is implemented yet.
