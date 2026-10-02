from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from src.config import get_settings
from src.db.postgres import create_pg_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
engine = create_pg_engine(settings.database_url_local, pool_size=2, max_overflow=1)


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url_local,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()