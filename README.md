# Dropbox Clone

Building a simplified version of Dropbox as part of a software engineering internship.

The main goal of this project is to change how I approach software engineering. Instead of jumping straight into coding and figuring things out as I go, I want to learn how to think about the system first, understand the requirements, make design decisions, and then start implementing.

I'm trying to move from a tactical, code-first approach to a more design first approach, where I focus on why I'm building something a certain way, not just how to make it work.
-----------
## Current Progress (PR 4)
It currently supports:

- Uploading and downloading files
- Updating, deleting, and renaming files
- File versioning
- Optimistic concurrency control
- Idempotent requests
- Transactional outbox and change journal
- Background event publishing with Redis and Celery
- Partitioned journal checkpoints
 
### New way of publishing

used a transactional outbox to make sure file changes are reliably recorded and published to the change journal

so when a file changes, its metadata and corresponding outbox event are saved in the same database transaction

background workers then publish pending events into the change journal

the journal is divided into four logical partitions each with its own sequence of positions

PostgreSQL row locking coordinates concurrent workers and prevents them from assigning duplicate positions.

redis acts as the message broker for celery, while celery beat schedules publishing tasks every five seconds

two celery workers process pending events with each task handling up to 100 events


## Setup and Running

### 1. Set up the project

```bash
git clone https://github.com/janaellweis/dropbox-clone.git
cd dropbox-clone
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start PostgreSQL

```bash
docker compose up -d
```

### 3. Apply database migrations

```bash
alembic upgrade head
```

### 4. Start Redis

For the first time:

```bash
docker run -d --name dropbox-redis -p 127.0.0.1:6379:6379 redis:7-alpine
```

If the container already exists:

```bash
docker start dropbox-redis
```

Verify the connection:

```bash
docker exec dropbox-redis redis-cli ping
```

Expected response: `PONG`.

### 5. Start FastAPI

```bash
python -m uvicorn backend.main:app --reload
```

API documentation: http://127.0.0.1:8000/docs
### 6. Start two Celery workers

The workers run in the background using `nohup`, so they can continue running after the terminal is closed.

Worker 1:

```bash
nohup python -m celery -A backend.workers.celery_app:celery_app worker --loglevel=info --queues=journal --hostname=worker1@%h --pool=solo > worker1.log 2>&1 &
```

Worker 2:

```bash
nohup python -m celery -A backend.workers.celery_app:celery_app worker --loglevel=info --queues=journal --hostname=worker2@%h --pool=solo > worker2.log 2>&1 &
```
### 7. Start Celery Beat

```bash
nohup python -m celery -A backend.workers.celery_app:celery_app beat --loglevel=info > beat.log 2>&1 &
```

Celery Beat schedules the publishing task every five seconds.

The workers and Beat run as background processes and do not require open terminal windows.

To check whether they are running:

```bash
pgrep -fl celery
```

These processes do not automatically restart after a reboot or crash.
## Next Steps

The next major step is completing the smart synchronization client.

The client will detect local file changes, retrieve remote updates using partitioned journal checkpoints, and decide which operations to perform.

It will also handle conflicting changes, preserve synchronization state across restarts, and safely recover from interrupted operations.

The existing client needs to be updated to work with the new partitioned checkpoint interface.