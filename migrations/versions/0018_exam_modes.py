"""Exam simulator phase 7b: code questions, written answers graded later, timed and other practice modes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f('ck_exam_questions_qtype_valid'), 'exam_questions', type_='check')
    op.create_check_constraint(op.f('ck_exam_questions_qtype_valid'), 'exam_questions', "qtype IN ('written', 'mcq', 'numeric', 'code')")
    op.add_column('practice_attempts', sa.Column('ends_at', sa.DateTime(timezone=True)))
    op.create_check_constraint(op.f('ck_practice_attempts_mode_valid'), 'practice_attempts',
                               "mode IN ('practice', 'timed', 'weakness', 'retake')")
    op.alter_column('practice_answers', 'score', nullable=True)  # NULL: not graded yet (timed, or the model is grading)
    op.add_column('practice_answers', sa.Column('feedback', JSONB()))


def downgrade():
    op.execute("DELETE FROM practice_answers WHERE score IS NULL")
    op.drop_column('practice_answers', 'feedback')
    op.alter_column('practice_answers', 'score', nullable=False)
    op.drop_constraint(op.f('ck_practice_attempts_mode_valid'), 'practice_attempts', type_='check')
    op.drop_column('practice_attempts', 'ends_at')
    op.execute("DELETE FROM exam_questions WHERE qtype = 'code'")
    op.drop_constraint(op.f('ck_exam_questions_qtype_valid'), 'exam_questions', type_='check')
    op.create_check_constraint(op.f('ck_exam_questions_qtype_valid'), 'exam_questions', "qtype IN ('written', 'mcq', 'numeric')")
