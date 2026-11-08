# Dropbox Clone

Building a simplified version of Dropbox as part of a software engineering internship.

The main goal of this project is to change how I approach software engineering. Instead of jumping straight into coding and figuring things out as I go, I want to learn how to think about the system first, understand the requirements, make design decisions, and then start implementing.

I'm trying to move from a tactical, code-first approach to a more design first approach, where I focus on why I'm building something a certain way, not just how to make it work.
-----------
## Current Progress BAckend MVP
It currently supports:

-File upload and download
-File updates, deletion, and renaming
-File versioning and version history
-SHA-256 content-addressed blob storage
-Content deduplication
-Soft deletion
-Optimistic concurrency control
-Idempotent requests using UUIDv7 request IDs
-Transactional outbox
-Asynchronous event publishing with Celery
-Ordered change journal with checkpoint-based pagination
-Recovery of expired worker claims
 
## Architecture

The backend is organized into separate layers:

- API Layer — FastAPI

Handles HTTP requests, input validation, responses, and error handling.

- Service Layer

Contains business logic for file operations, version management, concurrency checks, and idempotency.

- Database — PostgreSQL

Stores file metadata, versions, outbox events, and published journal entries.

- Blob Storage

Stores file content separately from metadata using SHA-256 hashes. File updates create new versions without overwriting previous blobs.

- Background Workers — Celery

Processes pending outbox events and publishes them to the change journal.

## Transactional Outbox and Change Journal

The transactional outbox ensures that file changes are reliably recorded for asynchronous publication.

When a file is created, updated, deleted, or renamed, the corresponding metadata changes and outbox event are committed within the same PostgreSQL transaction.

Celery workers periodically claim pending events and publish them to the change journal.

The implementation uses:

-PostgreSQL SELECT FOR UPDATE SKIP LOCKED for concurrent event claiming
-Leases to recover abandoned claims
-Per-file event sequences to preserve operation ordering
-PostgreSQL transaction-level advisory locking to serialize journal publication
-A single numeric journal checkpoint for client synchronization
-Clients retrieve changes through GET /updates, using their last processed checkpoint.
-Technology Stack
-Python 3.13
-FastAPI
-PostgreSQL
-SQLAlchemy
-Alembic
-Celery
-Kombu SQLAlchemy transport
-Pytest
-SHA-256

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

### 4. Start FastAPI

```bash
python -m uvicorn backend.main:app --reload
```
API documentation: http://127.0.0.1:8000/docs

## 5. Start Celery Worker

The workers run in the background using nohup, so they can continue running after the terminal is closed.

Worker 1:

nohup python -m celery -A backend.workers.celery_app:celery_app worker --loglevel=info --queues=journal --hostname=worker1@%h --pool=solo > worker1.log 2>&1 &

Worker 2:

nohup python -m celery -A backend.workers.celery_app:celery_app worker --loglevel=info --queues=journal --hostname=worker2@%h --pool=solo > worker2.log 2>&1 &

The backend supports multiple Celery workers processing outbox events concurrently.

Workers use PostgreSQL `SELECT FOR UPDATE SKIP LOCKED` to claim different events without processing the same event simultaneously.

A PostgreSQL advisory transaction lock ensures that only one worker publishes events to the change journal at a time, preserving checkpoint ordering.
Additional workers can be started with unique hostnames. Journal publication remains serialized to maintain consistency.

## 6. Start Celery Beat

nohup python -m celery -A backend.workers.celery_app:celery_app beat --loglevel=info > beat.log 2>&1 &

Celery Beat schedules the publishing task every five seconds.

The workers and Beat run as background processes and do not require open terminal windows.

To check whether they are running:

pgrep -fl celery

These processes do not automatically restart after a reboot or crash.

## Next Steps

The next phase focuses on building the synchronization client.

The client should:
-Detect local file changes using hashes
-Retrieve remote changes using journal checkpoints
-Determine the necessary synchronization operations
-Upload and download files
-Handle conflicts without losing local changes
-Preserve synchronization state across restarts
-Recover safely from interrupted operations
-The client will be designed around separate change detection, planning, execution, and coordination responsibilities.