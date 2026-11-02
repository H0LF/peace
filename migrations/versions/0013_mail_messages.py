"""University mailbox messages: headers, academic bodies and what PEACE moved to Deleted Items (phase 4)."""

from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    false = sa.text('false')
    op.create_table(
        'mail_messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('folder', sa.String(16), nullable=False),
        sa.Column('uidvalidity', sa.BigInteger(), nullable=False),
        sa.Column('uid', sa.BigInteger(), nullable=False),
        sa.Column('message_id', sa.String(998)),
        sa.Column('thread', sa.String(998)),
        sa.Column('sender', sa.String(320), server_default='', nullable=False),
        sa.Column('sender_name', sa.String(200), server_default='', nullable=False),
        sa.Column('subject', sa.Text(), server_default='', nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True)),
        sa.Column('size', sa.Integer(), server_default='0', nullable=False),
        sa.Column('seen', sa.Boolean(), server_default=false, nullable=False),
        sa.Column('flagged', sa.Boolean(), server_default=false, nullable=False),
        sa.Column('answered', sa.Boolean(), server_default=false, nullable=False),
        sa.Column('bulk', sa.Boolean(), server_default=false, nullable=False),
        sa.Column('notice', sa.Boolean(), server_default=false, nullable=False),
        sa.Column('academic', sa.Boolean(), server_default=false, nullable=False),
        sa.Column('course_id', sa.Integer(), sa.ForeignKey('courses.id', ondelete='SET NULL', name=op.f('fk_mail_messages_course_id_courses'))),
        sa.Column('body', sa.Text()),
        sa.Column('removed_at', sa.DateTime(timezone=True)),
        sa.Column('removed_by', sa.String(32)),
        sa.Column('trash_uidvalidity', sa.BigInteger()),
        sa.Column('trash_uid', sa.BigInteger()),
        sa.Column('purged_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('folder', 'uidvalidity', 'uid', name=op.f('uq_mail_messages_folder')),
    )
    for col in ('message_id', 'sent_at', 'course_id'):
        op.create_index(op.f(f'ix_mail_messages_{col}'), 'mail_messages', [col])


def downgrade():
    op.drop_table('mail_messages')
