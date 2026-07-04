from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.schemas import SyncStatus
from app.services.sync_job import get_sync_status, run_sync_once

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.get("/status", response_model=SyncStatus)
def sync_status(db: Session = Depends(get_db)):
    return get_sync_status(db)


@router.post("/run", response_model=SyncStatus)
def sync_run():
    return run_sync_once()
