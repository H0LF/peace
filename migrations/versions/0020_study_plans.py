"""A study plan per exam; its sessions are ordinary study sessions that also say what to do in them."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'study_plans',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('events.id', name=op.f('fk_study_plans_event_id_events'), ondelete='CASCADE'), nullable=False),
        sa.Column('topics', JSONB(), nullable=False),
        sa.Column('basis', sa.String(16), nullable=False),
        sa.Column('assumptions', JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('needed_minutes', sa.Integer(), nullable=False),
        sa.Column('placed_minutes', sa.Integer(), nullable=False),
        sa.Column('signature', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('event_id', name=op.f('uq_study_plans_event_id')),
    )
    op.add_column('study_sessions', sa.Column('plan_id', sa.Integer(), sa.ForeignKey('study_plans.id', name=op.f('fk_study_sessions_plan_id_study_plans'), ondelete='SET NULL')))
    op.create_index(op.f('ix_study_sessions_plan_id'), 'study_sessions', ['plan_id'])
    op.add_column('study_sessions', sa.Column('topic', sa.String(200)))
    op.add_column('study_sessions', sa.Column('activity', sa.String(16)))
    op.add_column('study_sessions', sa.Column('plan_materials', JSONB()))


def downgrade():
    op.drop_column('study_sessions', 'plan_materials')
    op.drop_column('study_sessions', 'activity')
    op.drop_column('study_sessions', 'topic')
    op.drop_index(op.f('ix_study_sessions_plan_id'), table_name='study_sessions')
    op.drop_column('study_sessions', 'plan_id')
    op.drop_table('study_plans')
