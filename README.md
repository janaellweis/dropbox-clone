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