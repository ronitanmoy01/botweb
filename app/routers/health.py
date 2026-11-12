from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session

router = APIRouter(tags=["ops"])


@router.get("/healthz")
async def healthz(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "error", "database": "unreachable"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "ok"})
