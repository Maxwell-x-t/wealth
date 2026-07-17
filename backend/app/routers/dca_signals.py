from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.schemas import DcaLiveSignal
from app.services.config import get_config_map
from app.services.dca_live_signal import build_dca_live_signal

router = APIRouter(prefix="/api/dca-signals", tags=["dca-signals"])


@router.get("/live", response_model=DcaLiveSignal)
def get_dca_live_signal(db: Session = Depends(get_db)):
    config = get_config_map(db)
    return build_dca_live_signal(db, config)
