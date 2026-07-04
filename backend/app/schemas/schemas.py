from datetime import date, datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class AccountOut(BaseModel):
    id: int
    name: str
    currency: str

    model_config = {"from_attributes": True}


class InstrumentBase(BaseModel):
    code: str
    name: str
    category: str
    account_id: int
    currency: str = "CNY"


class InstrumentCreate(InstrumentBase):
    pass


class InstrumentUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    account_id: Optional[int] = None
    currency: Optional[str] = None
    is_active: Optional[bool] = None


class InstrumentOut(InstrumentBase):
    id: int
    is_active: bool
    account_name: Optional[str] = None

    model_config = {"from_attributes": True}


class TransactionBase(BaseModel):
    trade_date: date
    account_id: int
    instrument_id: int
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)
    price: float = Field(ge=0)
    fee: float = Field(ge=0, default=0)
    exchange_rate: float = Field(gt=0, default=1)
    note: Optional[str] = None


class TransactionCreate(TransactionBase):
    pass


class TransactionUpdate(BaseModel):
    trade_date: Optional[date] = None
    account_id: Optional[int] = None
    instrument_id: Optional[int] = None
    side: Optional[Literal["buy", "sell"]] = None
    quantity: Optional[float] = Field(default=None, gt=0)
    price: Optional[float] = Field(default=None, ge=0)
    fee: Optional[float] = Field(default=None, ge=0)
    exchange_rate: Optional[float] = Field(default=None, gt=0)
    note: Optional[str] = None


class TransactionOut(TransactionBase):
    id: int
    amount: float
    amount_cny: float
    currency: str
    created_at: datetime
    account_name: Optional[str] = None
    instrument_name: Optional[str] = None
    instrument_code: Optional[str] = None

    model_config = {"from_attributes": True}


class PriceUpdate(BaseModel):
    instrument_id: int
    price: float = Field(gt=0)
    snapshot_date: Optional[date] = None


class PriceOut(BaseModel):
    instrument_id: int
    instrument_code: str
    instrument_name: str
    price: Optional[float] = None
    snapshot_date: Optional[date] = None
    currency: str


class PriceRefreshItem(BaseModel):
    instrument_id: int
    instrument_code: str
    instrument_name: str
    success: bool
    price: Optional[float] = None
    snapshot_date: Optional[date] = None
    currency: str
    source: Optional[str] = None
    error: Optional[str] = None


class PriceRefreshResult(BaseModel):
    success_count: int
    fail_count: int
    items: List[PriceRefreshItem]


class AllocationTarget(BaseModel):
    nasdaq: float = 70
    sp500: float = 30
    a_share: float = 0
    gold: float = 0
    cash: float = 0
    qdii: float = 0
    mainland: float = 60
    hk: float = 40
    usd_cny_rate: float = 7.2
    plan_start_date: Optional[date] = None
    building_first_month_amount: float = 100000
    building_monthly_amount: float = 50000
    building_months: int = 8
    dca_monthly_amount: float = 10000
    plan_horizon_years: int = 20
    mainland_nasdaq_code: str = "513100"
    mainland_sp500_code: str = "513500"
    hk_nasdaq_code: str = "QQQM"
    hk_sp500_code: str = "VOO"
    forecast_years: int = 20
    forecast_return_pessimistic: float = 4
    forecast_return_neutral: float = 8
    forecast_return_optimistic: float = 12

    @field_validator("nasdaq", "sp500", "a_share", "gold", "cash", "qdii")
    @classmethod
    def validate_allocation_ratio(cls, value: float) -> float:
        if value < 0 or value > 100:
            raise ValueError("比例必须在 0-100 之间")
        return value


class HoldingOut(BaseModel):
    instrument_id: int
    code: str
    name: str
    category: str
    account_id: int
    account_name: str
    currency: str
    quantity: float
    avg_cost: float
    total_cost: float
    current_price: float
    market_value: float
    market_value_cny: float
    unrealized_pnl: float
    unrealized_pnl_cny: float
    unrealized_pnl_rate: float
    realized_pnl: float
    realized_pnl_cny: float
    weight: float


class CategoryAllocation(BaseModel):
    category: str
    label: str
    current_pct: float
    target_pct: float
    gap_pct: float


class RebalanceRecommendation(BaseModel):
    account: str
    category: str
    label: str
    code: str
    name: str
    reason: str


class AccountAllocation(BaseModel):
    key: str
    label: str
    current_pct: float
    target_pct: float
    gap_pct: float


class RebalanceDetail(BaseModel):
    summary: Optional[str]
    index_gaps: List[CategoryAllocation]
    account_gaps: List[AccountAllocation]
    recommendations: List[RebalanceRecommendation]


class InvestmentPlanItem(BaseModel):
    plan_date: date
    phase: str
    phase_label: str
    week_index: int
    account: str
    category: str
    category_label: str
    target_label: str
    amount_cny: float
    base_amount_cny: float
    matched_amount_cny: float = 0
    shortfall_cny: float = 0
    rolled_over_amount_cny: float = 0
    rolled_over_count: int = 0
    status: str


class InvestmentPlanOverview(BaseModel):
    upcoming: List[InvestmentPlanItem]
    overdue_count: int
    overdue: List[InvestmentPlanItem]
    merged_count: int = 0
    building_total: int
    building_done: int
    dca_done: int
    dca_partial: int = 0
    dca_elapsed: int = 0
    dca_execution_rate: float = 0
    history: List[InvestmentPlanItem] = []
    next_item: Optional[InvestmentPlanItem]


class DashboardSummary(BaseModel):
    total_assets_cny: float
    net_investment_cny: float
    total_return_cny: float
    return_rate: Optional[float]
    realized_pnl_cny: float
    unrealized_pnl_cny: float
    xirr: Optional[float]
    annualized_return: Optional[float]
    mainland_assets_cny: float
    hk_assets_cny: float
    cny_assets: float
    usd_assets: float
    category_allocations: List[CategoryAllocation]
    rebalance_suggestion: Optional[str]
    rebalance: Optional[RebalanceDetail]
    holdings: List[HoldingOut]


class AssetSnapshotPoint(BaseModel):
    date: date
    total_assets_cny: float
    net_investment_cny: float
    total_return_cny: float


class ForecastPoint(BaseModel):
    year_offset: int
    year: int
    year_label: str
    assets_cny: float
    principal_cny: float
    profit_cny: float
    return_rate: Optional[float]


class ForecastScenario(BaseModel):
    key: str
    label: str
    annual_return_pct: float
    points: List[ForecastPoint]
    final_assets_cny: float
    final_principal_cny: float
    final_profit_cny: float
    final_return_rate: Optional[float]


class ForecastContributionYear(BaseModel):
    year: int
    amount_cny: float


class WealthForecast(BaseModel):
    current_assets_cny: float
    current_net_investment_cny: float
    years: int
    rates: Dict[str, float]
    contribution_by_year: List[ForecastContributionYear]
    scenarios: List[ForecastScenario]
