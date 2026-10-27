main goal isn't just to build a working project, I want to change how I approach software engineering, moving from jumping straight into coding to thinking about the design first


so I am trying to understand why we make certain decisions, how to structure a system, and how to think about problems before implementing them.

## Progress Checklist
### Backend

- [x] Set up FastAPI
- [x] Set up PostgreSQL
- [x] Design database models
- [x] Set up Alembic migrations
- [x] Understand how Alembic works internally (still need another explanation)
- [x] File upload and download
- [x] Basic file versioning structure
- [x] Content hashing using SHA-256
- [x] Soft deletion in the database design
- [x] Change journal
- [x] Separate API, service, database, and storage logic


### Client and Sync

- [x] Build a basic Python client that can push and pull files
- [ ] Detect local file changes
- [ ] Automatically sync between two clients
- [ ] Handle offline changes
- [ ] Handle conflicts
- [ ] Add file updates, deletion, and renaming

### Other Things To Do

- [ ] Add folder handling

## Learning to Think About Design First

biggest thing I'm trying to improve, my approach to projects was usually to figure out what needed to be done and start coding until it worked.

but now I'm trying to understand the problem first and think about how the different parts of the system should work together

### 1. Breaking the system into smaller parts

Instead of treating the Dropbox clone as one big project, I broke it down into components:

- Backend API
- Database for metadata
- Storage for file contents
- Service layer for the actual logic
- Client for uploading and downloading files
- Sync engine (next)

This helped me understand what each component is responsible for and how they communicate.

### 2. Database design decisions

When designing the database, I learned that choosing the right data types and constraints matters, not just deciding what tables to create.

**UUIDv7 vs UUIDv4**

used UUIDv7 for file and file version IDs instead of UUIDv4.

- UUIDv4 generates random IDs, so new records are inserted into different places in the database index.
- UUIDv7 includes a timestamp, meaning IDs are roughly ordered by creation time.
- This can improve index performance because new IDs are generally inserted near each other instead of randomly.

UUIDv7 still provides unique identifiers without needing the database to generate sequential IDs.

database design isnt just about storing data, but ragther the IDs, constraints, and relationships we choose affect performance, consistency, and how the rest of the system works.

### 3. Separating responsibilities

why we did we need different layers instead of putting everything in the API? cuz it makes it easier to change or test one part without affecting everything else.

- API: receives requests and sends responses.
- Service: handles the file operations and rules.
- Database: stores file information.
- Storage: saves the actual file content.

### 4. Thinking about failures before they happen
what happens when something goes wrong? many what if scnearios start pooping up when thinking during the code development like:

- What if the upload succeeds but the response gets lost?
- What if two clients upload to the same path at the same time?
- What if the database fails halfway through saving a file?
- What if two clients edit the same file?

designing a system means thinking about failure cases, not just the normal flow of events

### 5. Tactical vs. strategic programming

From *A Philosophy of Software Design*, I'm learning the difference between tactical and strategic programming.

Tactical programming focuses on getting the immediate task working, even if the design becomes more complicated later.

Strategic programming means spending more time thinking about the design and keeping the system understandable as it grows.

I'm trying to start asking "How should this work, and why?"

I still find this difficult, but it's something I want to improve throughout the internship.


## 6. Idempotency
used a unique request_id for each upload so the server can recognize retries

If the same request ID is sent again with the same data, the server returns the original result instead of creating another file.

If the same request ID is used with different data, the server rejects it

We also use a request fingerprint to check whether the repeated request actually contains the same data, because network failures can happen after the server processes a request but before the client receives the response

need to design for retries, not assume every request will succeed and return a response the first time

## 7. Optimistic concurrency control

Major problem to tackle was what happens when two clients edit the same file.

"expected_version" to check whether the client is updating the version it originally read.

so ifthe file has already changed, the server rejects the outdated update instead of silently overwriting newer content.

## 8. Change Journla

change journal to record operations such as CREATE, UPDATE, DELETE, and RENAME.

for the clients to request updates since their last checkpoint.

added pagination so clients can retrieve changes in smaller batches.

the order of journal entries matters so clients should not receive changes in the wrong order


## 9. Database Transactions

database operations need to happen together, updating a file can involve creating a new version, updating its metadata, and recording an event in the journal.

if one database operation fails, we don't want the others to remain partially committed.

Transactions allow the database changes to succeed or fail together.

## Struggles / Things I Need to Work On

- **System design:** I sometimes understand the coding part but struggle with deciding how the system should be structured.
- **Design principles:** Still learning how to separate responsibilities and avoid unnecessary complexity.
- **Git:** Need to get used to committing, pushing, pulling, and creating PRs regularly instead of waiting until the end.
- **Thinking before coding:** I still tend to jump into implementation instead of fully thinking through the design first, so I need to get better at making design decisions on my own, explaining why I chose a certain approach, and thinking about the system as a whole instead of just individual functions.

