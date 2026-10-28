# Dropbox Clone

Building a simplified version of Dropbox as part of a software engineering internship.

The main goal of this project is to change how I approach software engineering. Instead of jumping straight into coding and figuring things out as I go, I want to learn how to think about the system first, understand the requirements, make design decisions, and then start implementing.

I'm trying to move from a tactical, code-first approach to a more design first approach, where I focus on why I'm building something a certain way, not just how to make it work.

## Current Progress

I started by designing the database structure, separating file metadata from actual file storage, and organizing the backend into different layers so each part has its own responsibility.

I built the backend using FastAPI, PostgreSQL, and SQLAlchemy, with SHA-256 hashing for file storage.

The backend now supports uploading, downloading, updating, deleting, and renaming files. I also implemented file versioning, a change journal to track updates, optimistic concurrency control to prevent outdated changes from overwriting newer ones, and idempotency to safely handle repeated requests.

I started building a basic Python client that communicates with the backend, but the complete synchronization engine is still in progress.

## Next Steps

The next step is completing the smart client so that changes made in one local folder can automatically synchronize with another through the server.

This includes detecting local changes, handling conflicts, and making sure uploads, downloads, deletions, and renames are synchronized correctly between clients.

After that, I'll test the complete system using two client folders and prepare a final demo.


Set Up and Running

1. Set up the project

git clone https://github.com/janaellweis/dropbox-clone.git
cd dropbox-clone
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

2. Start the database
docker compose up -d
3. Run the migrations
alembic upgrade head
4. Start the backend
uvicorn backend.main:app --reload
The API will be available at http://127.0.0.1:8000/docs.
# Testing

I wrote tests for file storage, backend services, API endpoints, the change journal, and the Python client.

The tests cover situations like repeated requests, outdated versions, transaction failures, and concurrent operations.

To run the tests, first create the separate test database and apply the migrations:

docker compose exec db psql -U dropbox -d postgres -c "CREATE DATABASE dropbox_test;"
DATABASE_URL=postgresql+psycopg://dropbox:dropbox_dev@localhost:5432/dropbox_test alembic upgrade head
python -m pytest -q
ruff check .

Current result: 35 tests passing.

# Next Steps

The next step is completing the smart sync client so changes made in one folder can automatically synchronize with another through the server.



###### Client Side Implementation PR 3

Started implementing the client-side file tracking components. Added a local file scanner that uses SHA-256 hashing, state management to remember file metadata and synchronization checkpoints, and local change detection to identify file changes and renames.

Next step is connecting these components to the synchronization engine.