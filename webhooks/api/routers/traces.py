from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Trace
from db.session import get_db
from webhooks.utils import trace_to_dict

router = APIRouter(prefix="/traces", tags=["traces"])

@router.get("/{trace_id}")
async def get_trace(
    trace_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    trace = await db.get(Trace, trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="trace not found")
    return trace_to_dict(trace)
