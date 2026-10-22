
# FastAPI, the framework we will use to build our API.
from fastapi import FastAPI

# Get the file endpoints from our new Files module.
from server.modules.files.controller import router as files_router

# Get the API endpoints defined in routes/sync.py
from server.modules.sync.controller import router as sync_router

# Get the API endpoints defined in routes/versions.py
from server.modules.versions.controller import router as versions_router

# Get the API endpoints defined in routes/folders.py
from server.modules.folders.controller import router as folders_router

# The first FastAPI application.
app = FastAPI(title="Dropbox Clone API")

# Register the endpoints with the application.
app.include_router(sync_router)

app.include_router(files_router)

app.include_router(versions_router)

app.include_router(folders_router)

# GET endpoint at the root URL (/) to check if the server is running.
@app.get("/")
def home():
    # Return a JSON response if it's running.
    return {"message": "Dropbox Clone API is running"}
