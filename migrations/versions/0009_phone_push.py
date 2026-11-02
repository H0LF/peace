"""When a notification was sent to the phone (ntfy), so it is sent once."""

from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('notifications', sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('notifications', 'pushed_at')
