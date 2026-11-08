from backend.db.database import SessionLocal
from backend.workers.celery_app import celery_app
from backend.workers.outbox_worker import process_event_batch


@celery_app.task(name="backend.workers.tasks.publish_outbox")
def publish_outbox() -> int:
    published = 0

    with SessionLocal() as session:
        for _ in range(10):
            count = process_event_batch(session, batch_size=100)

            if count == 0:
                break

            published += count

    return published