from celery import Celery

from backend.db.database import engine

celery_app = Celery(
    "dropbox_sync",
    broker=f"sqla+{engine.url.render_as_string(hide_password=False)}",
    include=["backend.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_routes={
        "backend.workers.tasks.publish_outbox": {"queue": "journal"},
    },
    beat_schedule={
        "publish-outbox": {
            "task": "backend.workers.tasks.publish_outbox",
            "schedule": 5.0,
        },
    },
)
