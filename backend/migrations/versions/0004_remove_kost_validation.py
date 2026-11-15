"""Remove the retired KOST validation job journal."""
from alembic import op

revision = '0004_remove_kost_validation'
down_revision = '0003_ais_journal'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table('workflow_validation_jobs')


def downgrade():
    raise RuntimeError('KOST validation journal removal is intentionally irreversible')