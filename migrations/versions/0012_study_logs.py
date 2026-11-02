"""Study logs: time actually studied, from the timer or entered by hand (phase 2)."""

from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'study_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('session_id', sa.Integer(), sa.ForeignKey('study_sessions.id', ondelete='SET NULL', name=op.f('fk_study_logs_session_id_study_sessions'))),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='SET NULL', name=op.f('fk_study_logs_task_id_tasks'))),
        sa.Column('course_id', sa.Integer(), sa.ForeignKey('courses.id', ondelete='SET NULL', name=op.f('fk_study_logs_course_id_courses'))),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True)),
        sa.Column('origin', sa.String(8), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("origin IN ('timer', 'manual')", name=op.f('ck_study_logs_origin_valid')),
        sa.CheckConstraint('ended_at IS NULL OR ended_at > started_at', name=op.f('ck_study_logs_ends_after_start')),
        sa.CheckConstraint("ended_at IS NOT NULL OR origin = 'timer'", name=op.f('ck_study_logs_only_timer_runs')),
    )
    for col in ('session_id', 'task_id', 'course_id', 'started_at'):
        op.create_index(op.f(f'ix_study_logs_{col}'), 'study_logs', [col])
    op.create_index('uq_study_logs_one_running', 'study_logs', ['origin'], unique=True, postgresql_where=sa.text('ended_at IS NULL'))


def downgrade():
    op.drop_table('study_logs')
