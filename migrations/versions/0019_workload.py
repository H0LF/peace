"""Deliveries read from briefs (materials.briefs_at) and a study guide per exam."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('materials', sa.Column('briefs_at', sa.DateTime(timezone=True)))
    op.create_table(
        'study_guides',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('events.id', name=op.f('fk_study_guides_event_id_events'), ondelete='CASCADE'), nullable=False),
        sa.Column('minutes', sa.Integer(), nullable=False),
        sa.Column('topics', JSONB(), nullable=False),
        sa.Column('why', sa.Text(), server_default='', nullable=False),
        sa.Column('signature', sa.String(32), nullable=False),
        sa.Column('model', sa.String(64)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('event_id', name=op.f('uq_study_guides_event_id')),
    )


def downgrade():
    op.drop_table('study_guides')
    op.drop_column('materials', 'briefs_at')
