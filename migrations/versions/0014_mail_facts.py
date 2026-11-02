"""Dates the model found in academic emails (phase 4b): when each message was read, and what passed the checks."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('mail_messages', sa.Column('extracted_at', sa.DateTime(timezone=True)))
    op.add_column('mail_messages', sa.Column('facts', JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))


def downgrade():
    op.drop_column('mail_messages', 'facts')
    op.drop_column('mail_messages', 'extracted_at')
