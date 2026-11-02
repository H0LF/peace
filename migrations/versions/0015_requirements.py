"""Assignment requirements and project checks (phase 5): the brief a task's requirements come from, its project
folder, the requirements checklist and the sandboxed checks."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('brief_material_id', sa.Integer(), sa.ForeignKey(
        'materials.id', ondelete='SET NULL', name=op.f('fk_tasks_brief_material_id_materials'))))
    op.add_column('tasks', sa.Column('brief_version', sa.DateTime(timezone=True)))
    op.add_column('tasks', sa.Column('project_path', sa.Text()))
    def stamps():
        return [sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
                for name in ('created_at', 'updated_at')]
    op.create_table(
        'requirements',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='CASCADE', name=op.f('fk_requirements_task_id_tasks')), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('state', sa.String(16), server_default='unverified', nullable=False),
        sa.Column('verified_by', sa.String(64)),
        sa.Column('evidence', JSONB()),
        sa.Column('origin', sa.String(8), server_default='model', nullable=False),
        sa.Column('edited', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        *stamps(),
        sa.CheckConstraint("kind IN ('functional', 'constraint', 'deliverable', 'grading')", name=op.f('ck_requirements_kind_valid')),
        sa.CheckConstraint("state IN ('unverified', 'verified', 'missing')", name=op.f('ck_requirements_state_valid')),
    )
    op.create_index(op.f('ix_requirements_task_id'), 'requirements', ['task_id'])
    op.create_table(
        'checks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='CASCADE', name=op.f('fk_checks_task_id_tasks')), nullable=False),
        sa.Column('requirement_id', sa.Integer(), sa.ForeignKey('requirements.id', ondelete='SET NULL', name=op.f('fk_checks_requirement_id_requirements'))),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('command', sa.Text(), nullable=False),
        sa.Column('origin', sa.String(8), server_default='student', nullable=False),
        sa.Column('approved', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('last_run_at', sa.DateTime(timezone=True)),
        sa.Column('last_status', sa.String(8)),
        sa.Column('last_output', sa.Text()),
        *stamps(),
    )
    op.create_index(op.f('ix_checks_task_id'), 'checks', ['task_id'])


def downgrade():
    op.drop_table('checks')
    op.drop_table('requirements')
    op.drop_column('tasks', 'project_path')
    op.drop_column('tasks', 'brief_version')
    op.drop_column('tasks', 'brief_material_id')
