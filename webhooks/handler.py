from __future__ import annotations

import os
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

import logging
from fastapi import FastAPI, Response, status, Header, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware

from db.session import init_db
from webhooks.api.routers import webhooks, campaigns, leads, dev, traces

logger = logging.getLogger(__name__)

# Security gate
async def verify_api_key(request: Request, x_api_key: str = Header(None)):
    if request.method == "GET":
        # Public read access
        return
        
    admin_key = os.environ.get("ADMIN_API_KEY")
    if not admin_key:
        return
        
    if x_api_key != admin_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin Key required for this action"
        )

app = FastAPI(title="Conversion Engine Operator API")

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
async def health(auth: None = Depends(verify_api_key)) -> dict[str, str]:
    return {"status": "ok"}

@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)

# Include all routers with security check
app.include_router(webhooks.router, dependencies=[Depends(verify_api_key)])
app.include_router(campaigns.router, dependencies=[Depends(verify_api_key)])
app.include_router(leads.router, dependencies=[Depends(verify_api_key)])
app.include_router(dev.router, dependencies=[Depends(verify_api_key)])
app.include_router(traces.router, dependencies=[Depends(verify_api_key)])
