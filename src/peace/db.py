from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from peace.config import database_url
from peace.models import Event


@event.listens_for(Session, "do_orm_execute")
def _hide_cancelled(state: ORMExecuteState) -> None:
    """Cancelled events are gone for every query (calendar, planner, notifications, assistant...). Only code that must
    see them (the sync that keeps them cancelled) asks with execution_options(include_cancelled=True)."""
    if state.is_select and not state.execution_options.get("include_cancelled"):
        state.statement = state.statement.options(
            with_loader_criteria(Event, Event.cancelled.is_(False), include_aliases=True))


@lru_cache
def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True)


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
