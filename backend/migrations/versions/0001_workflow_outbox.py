"""Digest-bound workflow aggregates and transactional notification outbox."""
from alembic import op
import sqlalchemy as sa

revision='0001_workflow_outbox'
down_revision=None
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('workflow_aggregates',
        sa.Column('id',sa.Text(),primary_key=True),
        sa.Column('tenant',sa.Text(),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('body',sa.Text(),nullable=False),
        sa.Column('body_sha256',sa.String(64),nullable=False),
        sa.Column('sequence',sa.BigInteger(),sa.Identity(),nullable=False),
        sa.CheckConstraint('revision >= 0',name='workflow_revision_nonnegative'))
    op.create_index('workflow_tenant_sequence','workflow_aggregates',['tenant','sequence'])
    op.create_table('workflow_outbox',
        sa.Column('id',sa.Text(),primary_key=True),
        sa.Column('aggregate_id',sa.Text(),sa.ForeignKey('workflow_aggregates.id'),nullable=False),
        sa.Column('tenant',sa.Text(),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('position',sa.Integer(),nullable=False),
        sa.Column('payload',sa.Text(),nullable=False),
        sa.Column('payload_sha256',sa.String(64),nullable=False),
        sa.Column('state',sa.Text(),nullable=False,server_default='READY'),
        sa.Column('attempt',sa.Integer(),nullable=False,server_default='0'),
        sa.Column('available_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column('lease_until',sa.DateTime(timezone=True)),
        sa.Column('owner',sa.Text()),
        sa.Column('token_sha256',sa.String(64)),
        sa.CheckConstraint("state IN ('READY','LEASED','DELIVERED')",name='outbox_state_allowed'),
        sa.CheckConstraint('attempt >= 0',name='outbox_attempt_nonnegative'),
        sa.CheckConstraint('position >= 0',name='outbox_position_nonnegative'),
        sa.UniqueConstraint('aggregate_id','position',name='outbox_aggregate_position_unique'))
    op.create_index('outbox_tenant_delivery','workflow_outbox',['tenant','state','available_at'])


def downgrade():
    # Run only on a disposable or explicitly backed-up destination: destroys its data.
    op.drop_table('workflow_outbox')
    op.drop_table('workflow_aggregates')
