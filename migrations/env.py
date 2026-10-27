from alembic import context

from peace.db import get_engine
from peace.models import Base

with get_engine().connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
