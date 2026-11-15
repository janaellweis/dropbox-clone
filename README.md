## Dropbox Clone
Building a simplified version of Dropbox as part of a software engineering internship.

The main goal of this project is to change how I approach software engineering. Instead of jumping straight into coding and figuring things out as I go, I want to learn how to think about the system first, understand the requirements, make design decisions, and then start implementing.

I'm trying to move from a tactical, code-first approach to a more design first approach, where I focus on why I'm building something a certain way, not just how to make it work.

## Functionaliltiy

Two clients can sync files through the same server. If I create, edit, rename, or delete a file in one folder, the other client can pick up that change. The system also keeps file versions, detects conflicting edits, and can recover from some interrupted operations.

Instead of uploading an entire file every time it changes, it uses **FastCDC** to split files into chunks and transfer only the chunks the other side is missing.

## Architecture
The project is split into three main parts:

- **`backend/`** handles the API, file metadata, versions, chunk storage, and change notifications. PostgreSQL stores the metadata, while the actual chunks are stored separately on disk.
- **`client/`** watches a local folder, checks for remote changes, decides what needs to happen, and carries out the sync operations.
- **`shared/`** contains the FastCDC chunking logic used by both the client and server, so they split files the same way.

The client's sync logic is split into a few responsibilities:

- **Detection:** the scanner and Watchdog find local changes. The client also does periodic full scans in case an event was missed.
- **Planner:** compares the local files, the last synced state, and the server's current state to decide what action is needed. It doesn't perform the action itself.
- **Executor:** uploads, downloads, renames, or deletes files based on the plan.
- **Coordinator:** connects everything, saves progress, handles recovery, and replans after each action.

The client keeps its progress in `.sync_state.json`, including file identities, versions, hashes, a journal checkpoint, and pending operations. Upload snapshots are saved in `.sync-pending/`. These internal files aren't synchronized.

## Chunking and file transfers

I chose **content-defined chunking** because a small edit shouldn't mean transferring a whole large file again. FastCDC chooses chunk boundaries based on the content, which helps unchanged chunks stay reusable even when bytes are inserted or removed.

The current chunk sizes are:

- Minimum: **256 KiB**
- Average target: **1 MiB**
- Maximum: **4 MiB**

Each chunk is identified and checked using SHA-256. A **chunk manifest** keeps the chunks in order so the original file can be rebuilt correctly.

For uploads, the client first makes a stable snapshot of the file, checks which chunks the server already has, uploads the missing ones, and commits the manifest.

For downloads, the client checks whether it can reuse chunks from the existing local file, downloads what is missing, and builds a temporary file. It verifies the chunks and the complete file before replacing the destination. There isn't a permanent local chunk cache.

This reduces unnecessary transfers, although manifests and chunk indexes still take more memory as the number of chunks grows.

## Handling failures and conflicts

A lot of the design came from asking what happens when an operation doesn't go as expected.

- **Optimistic concurrency:** the server checks `expected_version` so an outdated update doesn't silently overwrite a newer version.
- **Idempotency:** the client keeps a request ID for retries. If the server completed an operation but the response was lost, the retry can refer to the same logical operation instead of creating another one.
- **Transactional outbox:** file metadata changes and their outbox events are saved in one database transaction. Celery workers later publish those events into the change journal, keeping per-file order and preventing duplicate journal publication.
- **Recovery:** the client saves pending operations and upload snapshots so it can work out what happened after a restart.
- **Conflicts:** when both sides change the same file, the client preserves conflicting content as a separate copy rather than automatically merging or overwriting it.

The backend uses SQLAlchemy connection pooling, with `pool_pre_ping=True` to check connections before reusing them. Celery Beat schedules outbox publication every five seconds, and the Celery broker uses PostgreSQL through Kombu's SQLAlchemy transport, so Redis isn't required.

Not every failure can be resolved automatically. Some ambiguous conflicts or invalidated operations still need intervention. Also, chunk files aren't part of PostgreSQL transactions, so failed operations can leave unreferenced chunks until garbage collection is added.


## Running the Project

These commands are for running the project locally. You'll need Python 3.13, Docker with PostgreSQL 17, and a filesystem that supports hard links.

Once the server, workers, and clients are running, file synchronization happens automatically.

### 1. Install dependencies

Run these commands from the project root:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 2. Start PostgreSQL

Start the database using Docker Compose:

```bash
docker compose up -d
```

Set the database and storage paths in each terminal that runs the backend:

```bash
export DATABASE_URL='postgresql+psycopg://dropbox:dropbox_dev@127.0.0.1:5432/dropbox'
export BLOB_STORAGE_PATH="$PWD/data/blobs"
```

Make sure these database credentials match the settings in `compose.yaml`.

### 3. Start the backend

Apply the database migrations:

```bash
python -m alembic upgrade head
```

Start the FastAPI server:

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
```

API documentation: http://127.0.0.1:8001/docs

### 4. Start Celery

Celery handles publishing file changes to the change journal, so other clients can detect them.

Start the worker in a separate terminal:

```bash
python -m celery -A backend.workers.celery_app:celery_app worker \
  --loglevel=info --queues=journal --pool=solo
```

Start Celery Beat in another terminal:

```bash
python -m celery -A backend.workers.celery_app:celery_app beat --loglevel=info
```

Make sure both terminals have the virtual environment activated and the same backend environment variables set.

Beat schedules the publishing task every five seconds, while the worker processes pending events.

### 5. Start two clients

Run each client in a separate terminal with a different folder.

Client A:

```bash
python -m client --folder /tmp/dropbox-example-a \
  --server http://127.0.0.1:8001 --interval 5
```

Client B:

```bash
python -m client --folder /tmp/dropbox-example-b \
  --server http://127.0.0.1:8001 --interval 5
```

Use fresh folders and activate the virtual environment in both terminals.

### 6. Try syncing files

Once everything is running, create a file in Client A's folder. The client will automatically detect and upload it, and Client B will download it after receiving the update.

You can also try editing, renaming, and deleting files to see how the clients synchronize.

The clients keep their synchronization state and pending operations on disk, allowing them to recover from supported interruptions after restarting.


## limitations
- Transfers are sequential, and large manifests can use significant memory.
- Storage garbage collection, more performance work, and production process management are future improvements.
