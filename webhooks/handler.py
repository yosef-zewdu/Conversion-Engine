from __future__ import annotations

import os
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

import logging
from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from db.session import init_db
from webhooks.api.routers import webhooks, campaigns, leads, dev, traces

logger = logging.getLogger(__name__)

app = FastAPI(title="Conversion Engine Operator API")

# Allow frontend dev server (localhost:5173 Vite default + any Render/Vercel origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    logger.info("Conversion Engine database initialized.")

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)

# Include all routers
app.include_router(webhooks.router)
app.include_router(campaigns.router)
app.include_router(leads.router)
app.include_router(dev.router)
app.include_router(traces.router)
