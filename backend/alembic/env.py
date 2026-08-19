"""Alembic environment.

The database URL is taken from config.py, never from alembic.ini, so
migrations and the running app can never disagree about which database they
are pointed at.
"""

from logging.config import fileConfig
from pathlib import Path
import sys

from sqlalchemy import engine_from_config, pool

from alembic import context

# backend/ on the path — this project uses flat modules (`from engine import
# ...`), and alembic runs from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from db import Base  # noqa: E402
import models  # noqa: F401,E402  (imported for its side effect: registering tables)

alembic_config = context.config
alembic_config.set_main_option("sqlalchemy.url", config.DATABASE_URL)

if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
