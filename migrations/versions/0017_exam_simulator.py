"""Past exams, the question bank and practice attempts (phase 7a, exam simulator)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None

KINDS = ("normalization", "er_model", "sql_query", "sql_ddl", "sql_dml", "transactions", "concurrency",
         "calculation", "proof", "code", "theory", "other")


def _in(column, values):
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _stamps():
    return [sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade():
    op.create_table(
        'past_exams',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('course_id', sa.Integer(), sa.ForeignKey('courses.id', ondelete='CASCADE', name=op.f('fk_past_exams_course_id_courses')), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('state', sa.String(10), server_default='new', nullable=False),
        sa.Column('error', sa.Text()),
        sa.Column('pages', JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        *_stamps(),
        sa.CheckConstraint(_in('state', ('new', 'reading', 'review', 'confirmed', 'error')), name=op.f('ck_past_exams_state_valid')),
    )
    op.create_index(op.f('ix_past_exams_course_id'), 'past_exams', ['course_id'])
    op.create_table(
        'exam_questions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('course_id', sa.Integer(), sa.ForeignKey('courses.id', ondelete='CASCADE', name=op.f('fk_exam_questions_course_id_courses')), nullable=False),
        sa.Column('past_exam_id', sa.Integer(), sa.ForeignKey('past_exams.id', ondelete='CASCADE', name=op.f('fk_exam_questions_past_exam_id_past_exams'))),
        sa.Column('concept_id', sa.Integer(), sa.ForeignKey('concepts.id', ondelete='SET NULL', name=op.f('fk_exam_questions_concept_id_concepts'))),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('number', sa.String(10), server_default='', nullable=False),
        sa.Column('weight', sa.Float()),
        sa.Column('kind', sa.String(16), server_default='other', nullable=False),
        sa.Column('qtype', sa.String(8), server_default='written', nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('printed', sa.Text(), server_default='', nullable=False),
        sa.Column('asks', sa.Text(), server_default='', nullable=False),
        sa.Column('page', sa.Integer()),
        sa.Column('options', JSONB()),
        sa.Column('answer', JSONB()),
        sa.Column('explanation', sa.Text(), server_default='', nullable=False),
        sa.Column('difficulty', sa.Float(), server_default='1', nullable=False),
        sa.Column('status', sa.String(10), server_default='draft', nullable=False),
        sa.Column('provenance', JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        *_stamps(),
        sa.CheckConstraint(_in('kind', KINDS), name=op.f('ck_exam_questions_kind_valid')),
        sa.CheckConstraint(_in('qtype', ('written', 'mcq', 'numeric')), name=op.f('ck_exam_questions_qtype_valid')),
        sa.CheckConstraint(_in('status', ('draft', 'ok', 'discarded')), name=op.f('ck_exam_questions_status_valid')),
        sa.CheckConstraint('difficulty >= 0.5 AND difficulty <= 2', name=op.f('ck_exam_questions_difficulty_range')),
    )
    for col in ('course_id', 'past_exam_id', 'concept_id'):
        op.create_index(op.f(f'ix_exam_questions_{col}'), 'exam_questions', [col])
    op.create_table(
        'practice_attempts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('course_id', sa.Integer(), sa.ForeignKey('courses.id', ondelete='CASCADE', name=op.f('fk_practice_attempts_course_id_courses')), nullable=False),
        sa.Column('mode', sa.String(10), server_default='practice', nullable=False),
        sa.Column('question_ids', JSONB(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
    )
    op.create_index(op.f('ix_practice_attempts_course_id'), 'practice_attempts', ['course_id'])
    op.create_table(
        'practice_answers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('attempt_id', sa.Integer(), sa.ForeignKey('practice_attempts.id', ondelete='CASCADE', name=op.f('fk_practice_answers_attempt_id_practice_attempts')), nullable=False),
        sa.Column('question_id', sa.Integer(), sa.ForeignKey('exam_questions.id', ondelete='CASCADE', name=op.f('fk_practice_answers_question_id_exam_questions')), nullable=False),
        sa.Column('response', JSONB(), nullable=False),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('grade', sa.String(10), nullable=False),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('attempt_id', 'question_id', name=op.f('uq_practice_answers_attempt_id')),
        sa.CheckConstraint("grade IN ('verified', 'estimated')", name=op.f('ck_practice_answers_grade_valid')),
        sa.CheckConstraint('score >= 0 AND score <= 1', name=op.f('ck_practice_answers_score_range')),
    )
    for col in ('attempt_id', 'question_id'):
        op.create_index(op.f(f'ix_practice_answers_{col}'), 'practice_answers', [col])


def downgrade():
    op.drop_table('practice_answers')
    op.drop_table('practice_attempts')
    op.drop_table('exam_questions')
    op.drop_table('past_exams')
