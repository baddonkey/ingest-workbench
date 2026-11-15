"""Explicit migrations only; never log database credentials or auto-upgrade an API."""
import os
from alembic import context
from sqlalchemy import create_engine
from app.postgres import metadata
from app import kost_validation

config=context.config


def run(connection):
    context.configure(connection=connection,target_metadata=metadata)
    with context.begin_transaction():context.run_migrations()


provided=config.attributes.get('connection')
if provided is not None:
    run(provided)
else:
    url=os.environ.get('MIGRATION_DATABASE_URL')
    if not url:raise RuntimeError('MIGRATION_DATABASE_URL is required')
    engine=create_engine(url)
    try:
        with engine.connect() as connection:run(connection)
    finally:engine.dispose()
