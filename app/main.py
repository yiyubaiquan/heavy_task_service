from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(
    title="Heavy Task Service",
    version="0.1.0",
    description="Redis-backed async service for heavy processing tasks.",
)

app.include_router(router)
