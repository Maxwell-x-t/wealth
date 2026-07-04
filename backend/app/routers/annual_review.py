from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.schemas import AnnualReview, AnnualReviewUpdate
from app.services.annual_review import get_annual_review, save_annual_review
from app.services.config import get_config_map
from app.services.fx_rate import get_latest_usd_cny_rate

router = APIRouter(prefix="/api/annual-review", tags=["annual-review"])


@router.get("", response_model=AnnualReview)
def read_annual_review(
    year: Optional[int] = Query(None, ge=2000, le=2100),
    db: Session = Depends(get_db),
):
    # 预热汇率，保证 hints 中市值相关口径一致
    config = get_config_map(db)
    config["usd_cny_rate"] = get_latest_usd_cny_rate(db)
    return get_annual_review(db, year or date.today().year)


@router.put("", response_model=AnnualReview)
def update_annual_review(payload: AnnualReviewUpdate, db: Session = Depends(get_db)):
    year = payload.year or date.today().year
    return save_annual_review(db, year, payload.checks)
