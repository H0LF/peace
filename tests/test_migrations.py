from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from conftest import ALEMBIC
from peace.db import get_engine
from peace.models import Base


def test_migrations_downgrade_and_upgrade_cleanly():
    command.downgrade(ALEMBIC, "base")
    command.upgrade(ALEMBIC, "head")


def test_models_match_migrations():
    with get_engine().connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == []
