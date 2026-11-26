from celery import Celery
from celery.schedules import crontab

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
        "backend.workers.tasks.collect_garbage": {"queue": "gc"},
    },
    beat_schedule={
        "collect-garbage": {
            "task": "backend.workers.tasks.collect_garbage",
            "schedule": crontab(hour=3, minute=0),
        },
        "publish-outbox": {
            "task": "backend.workers.tasks.publish_outbox",
            "schedule": 5.0,
        },
    },
)
