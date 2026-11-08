from fastapi import FastAPI

import backend.db.soft_delete  # noqa: F401
from backend.core.handlers import register_exception_handlers
from backend.routers.files import router as files_router
from backend.routers.mutations import router as mutations_router
from backend.routers.updates import router as updates_router

app = FastAPI(title="Dropbox Clone")
register_exception_handlers(app)

app.include_router(files_router)
app.include_router(mutations_router)
app.include_router(updates_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
