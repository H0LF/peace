"""Full reading of course files: what is inside an archive, copies of the same file, a summary, and the files a file refers to.

Revision ID: 0021
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('materials', sa.Column('parent_id', sa.Integer(), sa.ForeignKey('materials.id', name=op.f('fk_materials_parent_id_materials'), ondelete='CASCADE')))
    op.create_index(op.f('ix_materials_parent_id'), 'materials', ['parent_id'])
    op.add_column('materials', sa.Column('duplicate_of_id', sa.Integer(), sa.ForeignKey('materials.id', name=op.f('fk_materials_duplicate_of_id_materials'), ondelete='SET NULL')))
    op.add_column('materials', sa.Column('duplicate_kind', sa.String(16)))
    op.add_column('materials', sa.Column('summary', sa.Text()))
    op.add_column('materials', sa.Column('file_refs', JSONB()))


def downgrade():
    op.drop_column('materials', 'file_refs')
    op.drop_column('materials', 'summary')
    op.drop_column('materials', 'duplicate_kind')
    op.drop_column('materials', 'duplicate_of_id')
    op.drop_index(op.f('ix_materials_parent_id'), table_name='materials')
    op.drop_column('materials', 'parent_id')
