from celery import Celery

celery_app = Celery(
    "dropbox_sync",
    broker="redis://localhost:6379/0",
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
