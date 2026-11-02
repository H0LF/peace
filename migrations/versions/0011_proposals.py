"""Proposals: changes PEACE suggests and the student applies, rejects or undoes (ADR-020)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'proposals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('key', sa.String(200), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('status', sa.String(16), server_default='pending', nullable=False),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('reason', sa.Text(), server_default='', nullable=False),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='SET NULL', name=op.f('fk_proposals_task_id_tasks'))),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('events.id', ondelete='SET NULL', name=op.f('fk_proposals_event_id_events'))),
        sa.Column('change_id', sa.Integer(), sa.ForeignKey('changes.id', ondelete='SET NULL', name=op.f('fk_proposals_change_id_changes'))),
        sa.Column('evidence', JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('result', JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('pending', 'applied', 'rejected', 'superseded', 'undone')", name=op.f('ck_proposals_status_valid')),
        sa.UniqueConstraint('key', name=op.f('uq_proposals_key')),
    )
    op.create_index(op.f('ix_proposals_status'), 'proposals', ['status'])
    op.create_index(op.f('ix_proposals_task_id'), 'proposals', ['task_id'])
    op.create_index(op.f('ix_proposals_event_id'), 'proposals', ['event_id'])
    op.create_index(op.f('ix_proposals_created_at'), 'proposals', ['created_at'])


def downgrade():
    op.drop_table('proposals')
