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

Not every failure can be resolved automatically. Some ambiguous conflicts or invalidated operations still need intervention. Also, chunk files aren't part of PostgreSQL transactions, so failed operations can leave unreferenced chunks. Garbage collection periodically removes chunks that are no longer referenced, after a 30-day inactivity period.

## Running the Project with Docker

Docker Compose runs PostgreSQL, FastAPI, Celery Worker, Celery Beat, and two synchronization clients.

### First-Time Setup

To run the project, you need Docker and Docker Compose installed.

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

```bash
docker compose down
```

This stops and removes the project's containers and network while preserving the database and blob storage volumes.

## Limitations
- File transfers are sequential, and large manifests can use significant memory.
- Interrupted uploads can be recovered automatically, but interrupted downloads, renames, and deletes remain pending.
- More performance optimization, automatic recovery for local operations, and production process management are future improvements.