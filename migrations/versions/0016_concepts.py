"""Concepts, their prerequisites and graded answers about them (phase 6, ADR-022: Mastery)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'concepts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('course_id', sa.Integer(), sa.ForeignKey('courses.id', ondelete='CASCADE', name=op.f('fk_concepts_course_id_courses')), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), server_default='', nullable=False),
        sa.Column('origin', sa.String(8), server_default='model', nullable=False),
        sa.Column('evidence', JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('confidence', sa.SmallInteger()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('course_id', 'name', name=op.f('uq_concepts_course_id')),
    )
    op.create_index(op.f('ix_concepts_course_id'), 'concepts', ['course_id'])
    op.create_table(
        'concept_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('concept_id', sa.Integer(), sa.ForeignKey('concepts.id', ondelete='CASCADE', name=op.f('fk_concept_links_concept_id_concepts')), nullable=False),
        sa.Column('requires_id', sa.Integer(), sa.ForeignKey('concepts.id', ondelete='CASCADE', name=op.f('fk_concept_links_requires_id_concepts')), nullable=False),
        sa.Column('approved', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.UniqueConstraint('concept_id', 'requires_id', name=op.f('uq_concept_links_concept_id')),
        sa.CheckConstraint('concept_id <> requires_id', name=op.f('ck_concept_links_not_self')),
    )
    op.create_index(op.f('ix_concept_links_concept_id'), 'concept_links', ['concept_id'])
    op.create_table(
        'concept_answers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('concept_id', sa.Integer(), sa.ForeignKey('concepts.id', ondelete='CASCADE', name=op.f('fk_concept_answers_concept_id_concepts')), nullable=False),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('difficulty', sa.Float(), server_default='1', nullable=False),
        sa.Column('grade', sa.String(10), nullable=False),
        sa.Column('confirmed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('source', sa.String(200), nullable=False),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("grade IN ('verified', 'estimated')", name=op.f('ck_concept_answers_grade_valid')),
        sa.CheckConstraint('score >= 0 AND score <= 1', name=op.f('ck_concept_answers_score_range')),
        sa.CheckConstraint('difficulty >= 0.5 AND difficulty <= 2', name=op.f('ck_concept_answers_difficulty_range')),
    )
    for col in ('concept_id', 'answered_at'):
        op.create_index(op.f(f'ix_concept_answers_{col}'), 'concept_answers', [col])


def downgrade():
    op.drop_table('concept_answers')
    op.drop_table('concept_links')
    op.drop_table('concepts')
