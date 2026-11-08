from sqlalchemy import event
from sqlalchemy.orm import with_loader_criteria

from backend.db.database import AppSession
from backend.db.models import File


@event.listens_for(AppSession, "do_orm_execute")
def filter_deleted_files(execute_state):
    if not execute_state.is_select:
        return

    if execute_state.execution_options.get("include_deleted"):
        return

    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            File,
            lambda model: model.deleted_at.is_(None),
            include_aliases=True,
        )
    )
