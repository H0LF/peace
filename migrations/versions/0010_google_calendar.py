"""Two-way Google Calendar: cancelled events (imported rows the user deleted) and the PEACE <-> Google event links."""

from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('events', sa.Column('cancelled', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        'google_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('entity', sa.String(16), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=False),
        sa.Column('google_id', sa.String(255), nullable=False),
        sa.Column('etag', sa.String(255), nullable=False, server_default=''),
        sa.Column('payload_hash', sa.String(64), nullable=False, server_default=''),
        sa.Column('suppressed', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("entity IN ('event', 'task', 'session')", name=op.f('ck_google_links_entity_valid')),
        sa.UniqueConstraint('entity', 'entity_id', name=op.f('uq_google_links_entity')),
        sa.UniqueConstraint('google_id', name=op.f('uq_google_links_google_id')),
    )


def downgrade():
    op.drop_table('google_links')
    op.drop_column('events', 'cancelled')
