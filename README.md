# Dropbox Clone

Building a simplified version of Dropbox as part of a software engineering internship.

The main goal of this project is to change how I approach software engineering. Instead of jumping straight into coding and figuring things out as I go, I want to learn how to think about the system first, understand the requirements, make design decisions, and then start implementing.

I'm trying to move from a tactical, code-first approach to a more **design-first approach**, where I focus on why I'm building something a certain way, not just how to make it work.

## Current Progress

Thinking about how the system should work and breaking it down into smaller components before coding.

I designed the database structure, separated file metadata from actual file storage, and organized the backend into different layers so each part has its own responsibility.

Then I started implementing the system using FastAPI and PostgreSQL, with SHA-256 hashing for file storage and endpoints for uploading and downloading files.

I also built a basic Python client that can push and pull files from the server.

The next step is building the actual synchronization logic between clients.
