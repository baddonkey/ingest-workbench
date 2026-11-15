"""Durable advisory validation against the separately operated KOST service."""
from alembic import op
from app.kost_validation import runs, events

revision = '0005_kost_service'
down_revision = '0004_remove_kost_validation'
branch_labels = None
depends_on = None


def upgrade():
    runs.create(op.get_bind())
    events.create(op.get_bind())


def downgrade():
    events.drop(op.get_bind())
    runs.drop(op.get_bind())