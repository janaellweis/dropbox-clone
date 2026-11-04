from backend.db.database import SessionLocal
from backend.workers.celery_app import celery_app
from backend.workers.outbox_worker import process_event_batch


@celery_app.task(name="backend.workers.tasks.publish_outbox")
def publish_outbox() -> int:
    with SessionLocal() as session:
        return process_event_batch(session, batch_size=100)
