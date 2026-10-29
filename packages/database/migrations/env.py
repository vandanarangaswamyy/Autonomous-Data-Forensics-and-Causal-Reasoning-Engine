from alembic import context
from atlas_database.connection import engine_for
from atlas_database.schema import metadata
from atlas_simulator.settings import SimulatorSettings

connection = context.config.attributes.get("connection")
if connection is not None:
    context.configure(connection=connection, target_metadata=metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    url = SimulatorSettings().database_url.get_secret_value()
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
    else:
        engine = engine_for(url)
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
        engine.dispose()
