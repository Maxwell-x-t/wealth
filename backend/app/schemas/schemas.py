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
    plan_phase: Optional[Literal["building", "dca"]] = None


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
    plan_phase: Optional[Literal["building", "dca"]] = None


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
    building_target_amount: Optional[float] = None
    dca_monthly_amount: float = 10000
    weeks_per_month: int = 4
    plan_horizon_years: int = 20
    mainland_nasdaq_code: str = "513100"
    mainland_sp500_code: str = "513500"
    hk_nasdaq_code: str = "QQQM"
    hk_sp500_code: str = "VOO"
    forecast_years: int = 20
    forecast_return_pessimistic: float = 4
    forecast_return_neutral: float = 8
    forecast_return_optimistic: float = 12
    forecast_inflation_pct: float = 2
    forecast_mc_volatility: float = 15
    forecast_mc_paths: int = 500
    sync_enabled: bool = False
    sync_interval_hours: int = 24
    plan_rebalance_enabled: bool = True
    plan_rebalance_threshold: float = 5
    dca_boost_enabled: bool = True
    dca_boost_20_pct_amount: float = 10000
    dca_boost_30_pct_amount: float = 20000
    dca_boost_40_pct_amount: float = 30000
    dca_boost_monthly_cap: float = 30000
    dca_boost_cash_available: float = 0
    dca_boost_lookback_days: int = 365
    hk_whole_share_only: bool = True
    hk_share_price_buffer_pct: float = 2
    mainland_plan_start_date: Optional[date] = None
    mainland_building_first_month_amount: Optional[float] = None
    mainland_building_monthly_amount: Optional[float] = None
    mainland_building_months: Optional[int] = None
    mainland_building_target_amount: Optional[float] = None
    mainland_dca_monthly_amount: Optional[float] = None
    mainland_weeks_per_month: Optional[int] = None
    mainland_nasdaq: Optional[float] = None
    mainland_sp500: Optional[float] = None
    mainland_dca_effective_from: Optional[date] = None
    hk_plan_start_date: Optional[date] = None
    hk_building_first_month_amount: Optional[float] = None
    hk_building_monthly_amount: Optional[float] = None
    hk_building_months: Optional[int] = None
    hk_building_target_amount: Optional[float] = None
    hk_dca_monthly_amount: Optional[float] = None
    hk_weeks_per_month: Optional[int] = None
    hk_nasdaq: Optional[float] = None
    hk_sp500: Optional[float] = None
    hk_dca_effective_from: Optional[date] = None
    dca_effective_from: Optional[date] = None

    @field_validator("nasdaq", "sp500", "a_share", "gold", "cash", "qdii")
    @classmethod
    def validate_allocation_ratio(cls, value: float) -> float:
        if value < 0 or value > 100:
            raise ValueError("比例必须在 0-100 之间")
        return value

    @field_validator(
        "plan_start_date",
        "mainland_plan_start_date",
        "hk_plan_start_date",
        "mainland_dca_effective_from",
        "hk_dca_effective_from",
        "dca_effective_from",
        "building_target_amount",
        "mainland_building_first_month_amount",
        "mainland_building_monthly_amount",
        "mainland_building_months",
        "mainland_building_target_amount",
        "mainland_dca_monthly_amount",
        "mainland_weeks_per_month",
        "mainland_nasdaq",
        "mainland_sp500",
        "hk_building_first_month_amount",
        "hk_building_monthly_amount",
        "hk_building_months",
        "hk_building_target_amount",
        "hk_dca_monthly_amount",
        "hk_weeks_per_month",
        "hk_nasdaq",
        "hk_sp500",
        mode="before",
    )
    @classmethod
    def empty_optional_to_none(cls, value):
        if value == "":
            return None
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
    account_index_gaps: Dict[str, List[CategoryAllocation]] = {}
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
    adjustment_note: Optional[str] = None
    currency: str = "CNY"
    whole_share_mode: bool = False
    execution_pool_usd: float = 0
    share_reference_price_usd: float = 0
    share_threshold_usd: float = 0
    executable_shares: int = 0
    credit_offset_cny: float = 0
    month_base_cny: float = 0
    month_net_cny: float = 0
    month_matched_cny: float = 0
    month_remaining_cny: float = 0
    is_month_anchor: bool = False
    dca_boost_cny: float = 0
    month_boost_cny: float = 0


class DcaBoostStatus(BaseModel):
    enabled: bool = False
    tier: Optional[int] = None
    tier_amount: float = 0
    applied_amount: float = 0
    cash_available: float = 0
    max_drawdown_pct: float = 0
    trigger_category: Optional[str] = None
    drawdowns: Dict[str, float] = {}
    note: Optional[str] = None


class PhaseInvestment(BaseModel):
    building_invested_cny: float = 0
    dca_invested_cny: float = 0
    other_invested_cny: float = 0
    building_matched_cny: float = 0
    dca_matched_cny: float = 0
    building_planned_cny: float = 0
    dca_planned_cny: float = 0


class AccountPlanSummary(BaseModel):
    account: str
    currency: str = "CNY"
    building_total: int
    building_done: int
    dca_elapsed: int
    dca_done: int
    dca_partial: int = 0
    dca_execution_rate: float = 0
    overdue_count: int = 0
    building_invested: float = 0
    dca_invested: float = 0
    building_invested_cny: float = 0
    dca_invested_cny: float = 0
    building_matched_cny: float = 0
    dca_matched_cny: float = 0
    building_target_amount: Optional[float] = None
    building_planned_amount: float = 0
    next_item: Optional[InvestmentPlanItem] = None


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
    phase_investment: PhaseInvestment = PhaseInvestment()
    account_summaries: List[AccountPlanSummary] = []
    dca_boost: Optional[DcaBoostStatus] = None
    dca_tilt_active: bool = False


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
    account_category_allocations: Dict[str, List[CategoryAllocation]] = {}
    rebalance_suggestion: Optional[str]
    rebalance: Optional[RebalanceDetail]
    holdings: List[HoldingOut]
    phase_investment: PhaseInvestment = PhaseInvestment()


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
    assets_real_cny: Optional[float] = None
    principal_real_cny: Optional[float] = None
    profit_real_cny: Optional[float] = None
    return_rate_real: Optional[float] = None


class ForecastScenario(BaseModel):
    key: str
    label: str
    annual_return_pct: float
    points: List[ForecastPoint]
    final_assets_cny: float
    final_principal_cny: float
    final_profit_cny: float
    final_return_rate: Optional[float]
    final_assets_real_cny: Optional[float] = None
    final_principal_real_cny: Optional[float] = None
    final_profit_real_cny: Optional[float] = None
    final_return_rate_real: Optional[float] = None


class ForecastContributionYear(BaseModel):
    year: int
    amount_cny: float
    building_cny: float = 0
    dca_cny: float = 0


class MonteCarloPoint(BaseModel):
    year_offset: int
    year: int
    year_label: str
    p10_cny: float
    p50_cny: float
    p90_cny: float
    p10_real_cny: Optional[float] = None
    p50_real_cny: Optional[float] = None
    p90_real_cny: Optional[float] = None


class MonteCarloResult(BaseModel):
    paths: int
    mean_return_pct: float
    volatility_pct: float
    points: List[MonteCarloPoint]
    final_p10_cny: float
    final_p50_cny: float
    final_p90_cny: float
    final_p10_real_cny: Optional[float] = None
    final_p50_real_cny: Optional[float] = None
    final_p90_real_cny: Optional[float] = None


class WealthForecast(BaseModel):
    current_assets_cny: float
    current_net_investment_cny: float
    years: int
    rates: Dict[str, float]
    use_inflation: bool = False
    inflation_pct: Optional[float] = None
    use_monte_carlo: bool = False
    contribution_by_year: List[ForecastContributionYear]
    scenarios: List[ForecastScenario]
    monte_carlo: Optional[MonteCarloResult] = None


class RiskPoint(BaseModel):
    year_offset: int
    assets_cny: float


class RiskScenario(BaseModel):
    key: str
    label: str
    drawdown_pct: float
    assets_after_crash_cny: float
    loss_cny: float
    recovery_years: Optional[int]
    recovery_return_pct: float
    horizon_years: int
    final_assets_cny: float
    final_principal_cny: float
    final_profit_cny: float
    final_return_rate: Optional[float]
    points: List[RiskPoint]


class RiskSimulation(BaseModel):
    current_assets_cny: float
    current_net_investment_cny: float
    annual_contribution_cny: float
    recovery_return_pct: float
    horizon_years: int
    custom_scenarios: List[RiskScenario]
    historical_scenarios: List[RiskScenario]


class AnnualReviewItem(BaseModel):
    key: str
    label: str
    checked: bool


class AnnualReviewHint(BaseModel):
    key: str
    level: str
    text: str


class AnnualReview(BaseModel):
    year: int
    items: List[AnnualReviewItem]
    done_count: int
    total_count: int
    completion_rate: float
    hints: List[AnnualReviewHint]


class AnnualReviewUpdate(BaseModel):
    year: Optional[int] = None
    checks: Dict[str, bool]


class SyncStatus(BaseModel):
    enabled: bool
    interval_hours: int
    scheduler_alive: bool
    running: bool
    last_run_at: Optional[str] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    prices_success: int = 0
    prices_fail: int = 0
    fx_rate: Optional[float] = None
    fx_source: Optional[str] = None


class BacktestCurrencySummary(BaseModel):
    currency: str
    account_label: str
    total_contributed: float
    final_assets: float
    profit: float
    return_rate: Optional[float] = None
    cagr: Optional[float] = None
    max_drawdown_pct: float = 0


class BacktestCurrencyPoint(BaseModel):
    principal: float
    assets: float
    profit: float
    return_rate: Optional[float] = None


class BacktestPoint(BaseModel):
    year: int
    month: int
    label: str
    nasdaq_price: float
    sp500_price: float
    contribution_cny: float = 0
    contribution_usd: float = 0
    currencies: Dict[str, BacktestCurrencyPoint]


class BacktestYearlyContribution(BaseModel):
    year: int
    cny_total: float = 0
    cny_building: float = 0
    cny_dca: float = 0
    usd_total: float = 0
    usd_building: float = 0
    usd_dca: float = 0


class BacktestPlanSettings(BaseModel):
    building_first_month_amount: float
    building_monthly_amount: float
    building_months: int
    building_target_amount: Optional[float] = None
    dca_monthly_amount: float
    currency: str


class HistoricalBacktest(BaseModel):
    start_date: date
    end_date: date
    nasdaq_symbol: str
    sp500_symbol: str
    nasdaq_weight_pct: float
    sp500_weight_pct: float
    strategy: str
    currency_summaries: List[BacktestCurrencySummary]
    points: List[BacktestPoint]
    yearly_contributions: List[BacktestYearlyContribution]
    plan_settings: Dict[str, BacktestPlanSettings]
