"""Durable KOST journal with retained capabilities and pinned execution evidence."""
from alembic import op
import sqlalchemy as sa

revision='0002_validation_jobs'
down_revision='0001_workflow_outbox'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('workflow_validation_jobs',
        sa.Column('id',sa.Text(),primary_key=True),sa.Column('tenant',sa.Text(),nullable=False),
        sa.Column('transfer_id',sa.Text(),sa.ForeignKey('workflow_aggregates.id'),nullable=False),
        sa.Column('sip_id',sa.Text(),nullable=False),sa.Column('state',sa.Text(),nullable=False),
        sa.Column('lease_until',sa.Float(),nullable=False),sa.Column('body',sa.Text(),nullable=False),
        sa.Column('body_sha256',sa.String(64),nullable=False),sa.Column('sequence',sa.BigInteger(),sa.Identity(),nullable=False),
        sa.CheckConstraint("state IN ('QUEUED','LEASED','DONE','CANCELLED')",name='validation_state_allowed'))
    op.create_index('validation_claim_order','workflow_validation_jobs',['tenant','state','sequence'])
    op.create_index('validation_one_pending_sip','workflow_validation_jobs',['tenant','transfer_id','sip_id'],unique=True,
        postgresql_where=sa.text("state IN ('QUEUED','LEASED')"))


def downgrade():op.drop_table('workflow_validation_jobs')
