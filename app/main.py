from __future__ import annotations

import os

from fastapi.responses import StreamingResponse
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .config import settings

from .graph.graph import ask_question, resume_question
from app.file.storage import FileRegistry
from .rest.ask_events_service import ask_events_service, resume_events_service
from .rest.ask_request import AskRequest
from .rest.ask_service import ask_service
from .rest.files_service import upload_files_service
import logging

from .rest.resume_service import resume_service

app = FastAPI(title="Schema Agent Backend", version="0.1.0")

registry = FileRegistry(
    registry_path=settings.registry_path,
    upload_dir=settings.upload_dir,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

class ResumeRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    chat_id: str = Field(min_length=1)
    human_answer: str


@app.on_event("startup")
async def startup_event():
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(settings.upload_dir, exist_ok=True)


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}

@app.post("/files/upload", tags=["Files"])
async def upload_files(files: list[UploadFile] = File(...)):
    return await upload_files_service(files, registry)

@app.get("/files", tags=["Files"])
async def list_files():
    files = registry.list_files()
    return {
        "status": "ok",
        "files": files,
        "file_count": len(files),
    }

@app.delete("/files", tags=["Files"])
async def clear_files():
    registry.clear_files()
    return {
        "status": "ok",
        "message": "All active schema files were removed.",
    }

@app.post("/ask", tags=["NL2SQL Pipeline"])
async def ask(request: AskRequest):
    return ask_service(request, registry)

#@app.post("/ask/events", tags=["NL2SQL Pipeline"])
async def ask_events(request: AskRequest):
    return await ask_events_service(request, registry)

@app.post("/ask/stream", tags=["NL2SQL Pipeline"])
async def ask_stream(request: AskRequest):
    return await ask_events_service(request, registry)

@app.post("/resume", tags=["NL2SQL Pipeline"])
async def resume(request: ResumeRequest):
    return resume_service(request=request)

@app.post("/resume/stream", tags=["NL2SQL Pipeline"])
async def resume_stream(request: ResumeRequest):
    return await resume_events_service(request)
