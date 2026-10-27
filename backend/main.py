from fastapi import FastAPI

from backend.routers.files import router as files_router
from backend.routers.mutations import router as mutations_router
from backend.routers.updates import router as updates_router

app = FastAPI(title="Dropbox Clone")

app.include_router(files_router)
app.include_router(mutations_router)
app.include_router(updates_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
