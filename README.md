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

The client's logic is separated into a few responsibilities:

- **Detection:** the scanner and Watchdog detect local changes. Events are grouped together using debouncing to avoid unnecessary sync cycles. A full scan on startup detects changes made while the client was offline.
- **Planner:** compares the local files, the last synced state, and the server's current state to decide what action is needed. It doesn't perform the action itself.
- **Executor:** carries out the planned uploads, downloads, renames, and deletes.
- **Coordinator:** connects everything, processes remote changes, and replans after each action.
- **Recovery:** checks unfinished operations after a restart and determines whether they can be safely completed or retried.
- **Storage:** manages persistent sync state, upload snapshots, file verification, and filesystem durability.

The goal of separating these responsibilities is to reduce complexity and make each part easier to understand, test, and modify independently.

### Client state storage

The client originally stored its state in a JSON file. I replaced this with SQLite because rewriting the entire state file after every operation was unnecessary, and I wanted better transactional guarantees.

The client now stores its progress in `.sync_state.db`, including:

- Local file identities, versions, hashes, and sizes.
- The last known remote file state.
- The change journal checkpoint.
- Pending synchronization operations.

SQLite uses Write-Ahead Logging (WAL) with full synchronous mode to provide durable database transactions.

Related state changes are committed together. For example, completing an operation updates the file state and removes the pending operation in the same transaction.

Upload snapshots are still saved in `.sync-pending/`. The database, its internal files, and upload snapshots are excluded from synchronization.

## Chunking and file transfers
Chunk uploads and downloads are now concurrent instead of sequential. The client uses a thread pool with up to four workers to transfer chunks in parallel.

The number of concurrent transfers is bounded to avoid loading too much data into memory or sending too many requests at once.

Even though downloads happen concurrently, chunks are written to the temporary file in manifest order so the original file is reconstructed correctly.

Each downloaded chunk is verified individually, and the complete file is verified before replacing the destination.

## Handling failures and conflicts

A lot of the design came from asking what happens when an operation doesn't go as expected.

- **Optimistic concurrency:** the server checks `expected_version` so an outdated update doesn't silently overwrite a newer version.
- **Idempotency:** the client keeps a request ID for retries. If the server completed an operation but the response was lost, the retry can refer to the same logical operation instead of creating another one.
- **Transactional outbox:** file metadata changes and their outbox events are saved in one database transaction. Celery workers later publish those events into the change journal, keeping per-file order and preventing duplicate journal publication.
- **Recovery:** before performing an operation, the client records its intent in SQLite. If the client crashes, it can inspect the pending operation and the actual filesystem state after restarting. Interrupted uploads reuse the original request ID and snapshot. Interrupted local downloads, renames, deletes, and conflict preservation operations can also be recovered when their state can be safely verified. Ambiguous operations remain pending instead of risking data loss.

- **Conflicts:** when both sides change the same file, the client preserves conflicting content as a separate copy rather than automatically merging or overwriting it.

The backend uses SQLAlchemy connection pooling, with `pool_pre_ping=True` to check connections before reusing them. Celery Beat schedules outbox publication every five seconds, and the Celery broker uses PostgreSQL through Kombu's SQLAlchemy transport, so Redis isn't required.

Not every failure can be resolved automatically. Some ambiguous conflicts or invalidated operations still need intervention. Also, chunk files aren't part of PostgreSQL transactions, so failed operations can leave unreferenced chunks. Garbage collection periodically removes chunks that are no longer referenced, after a 30-day inactivity period.

## Running the client as a background process

The client can run as a background process on macOS using launchd, without needing to keep a terminal open.

The daemon supports:

- Starting automatically at login.
- Restarting after unexpected process failures.
- Graceful shutdown.
- Persistent logs.
- Reconnecting after temporary network failures.
- Detecting changes made while the client was offline.

The client keeps its progress in SQLite, so restarting the process doesn't mean starting synchronization from scratch.

## Running the Project

### First-Time Setup

To run the project, you need Docker and Docker Compose installed. Python 3.13 and the project dependencies are also required if you want to run the client directly on your machine.

1. Clone the repository and open the project folder.

   ```bash
   git clone https://github.com/janaellweis/dropbox-clone.git
   cd dropbox-clone
   ```

2. Create two folders for the synchronization clients.

   ```bash
   mkdir -p client-a client-b
   ```

3. Build and start the services.

   ```bash
   docker compose up -d --build
   ```

   Docker Compose starts PostgreSQL, runs the database migrations, and starts FastAPI, Celery Worker, Celery Beat, and both clients.

4. Check that the services are running.

   ```bash
   docker compose ps
   ```

The API documentation is available at http://localhost:8001/docs.

### Testing File Synchronization

Once both clients are running, create a file in the first client's folder:

```bash
echo "Hello Dropbox" > client-a/hello.txt
```

The file should automatically appear in `client-b/hello.txt` after synchronization.

You can also edit, rename, or delete files in either folder. The other client should eventually reflect those changes.

To monitor synchronization:

```bash
docker compose logs -f client-a client-b
```

### Running a Native Client

The client can also run directly on your machine instead of inside Docker. This allows you to synchronize an actual local folder with the server.

Make sure the backend services are running and the Python environment is activated.

```bash
python -m client \
    --folder ~/dropbox-docker-test \
    --server http://127.0.0.1:8001 \
    --interval 5
```

The client watches the folder for local changes and checks the server for remote changes every five seconds.

Press `Ctrl+C` to stop the client.

### Running the Client as a Daemon

On macOS, the client can run in the background using launchd without needing to keep a terminal open.

The daemon supports automatic startup at login, restarting after unexpected failures, shutdown handling, and persistent logs.

To view the first-time installation options:

```bash
python -m scripts.sync_agent install --help
```

Once installed, the daemon can be managed using the following commands.

**Start the daemon:**

```bash
python -m scripts.sync_agent start
```

**Check its status:**

```bash
python -m scripts.sync_agent status
```

**Stop the daemon:**

```bash
python -m scripts.sync_agent stop
```

**View client logs:**

```bash
tail -f ~/Library/Logs/DropboxClone/stdout.log
```

**View error logs:**

```bash
tail -f ~/Library/Logs/DropboxClone/stderr.log
```

The client stores its progress in SQLite, so it can continue synchronization after restarting. A full scan on startup detects changes made while the client was offline.

Only one client process should manage a particular local folder at a time.

### Managing the Project

**Start the services:**

```bash
docker compose up -d
```

**Rebuild after changing the code:**

```bash
docker compose up -d --build
```

**Check running services:**

```bash
docker compose ps
```

**View all service logs:**

```bash
docker compose logs -f
```

You can also view logs for a specific service:

```bash
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f beat
docker compose logs -f client-a
docker compose logs -f client-b
```

**Stop the project:**

If the native daemon is running, stop it first:

```bash
python -m scripts.sync_agent stop
```

Then stop the Docker services:

```bash
docker compose down
```

This stops and removes the project's containers and network while preserving the database and blob storage volumes.

## Limitations

- Background process management currently supports macOS.
- The system has not been tested at production scale.