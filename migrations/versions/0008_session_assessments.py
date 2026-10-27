"""Link preparation sessions to assessments without synthesizing tasks or changing existing data."""

from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('study_sessions', sa.Column('event_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_study_sessions_event_id_events', 'study_sessions', 'events', ['event_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_study_sessions_event_id', 'study_sessions', ['event_id'])
    op.add_column('study_sessions', sa.Column('progress_credit', sa.SmallInteger(), server_default='0', nullable=False))
    # Historical contributions cannot be reconstructed after arbitrary manual progress edits.
    op.execute("UPDATE study_sessions SET progress_credit = -1 WHERE status = 'done'")


def downgrade():
    op.drop_column('study_sessions', 'progress_credit')
    op.drop_index('ix_study_sessions_event_id', table_name='study_sessions')
    op.drop_constraint('fk_study_sessions_event_id_events', 'study_sessions', type_='foreignkey')
    op.drop_column('study_sessions', 'event_id')
