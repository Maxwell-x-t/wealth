from fastapi import APIRouter, HTTPException

from app.schemas.schemas import IndexDataRefreshRequest, IndexDataRefreshResult, IndexDataStatus
from app.services.index_data_refresh import get_index_data_status, refresh_index_data

router = APIRouter(prefix="/api/index-data", tags=["index-data"])


@router.get("/status", response_model=IndexDataStatus)
def index_data_status():
    return get_index_data_status()


@router.post("/refresh", response_model=IndexDataRefreshResult)
def index_data_refresh(payload: IndexDataRefreshRequest):
    if not payload.symbols:
        raise HTTPException(status_code=400, detail="请至少选择一个指数")
    return refresh_index_data(payload.symbols, timeout=payload.timeout)
